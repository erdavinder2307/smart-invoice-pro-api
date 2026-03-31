from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container, refresh_tokens_container
import uuid
import os
import secrets
from flasgger import swag_from
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import datetime

api_blueprint = Blueprint('api_core', __name__)
auth_blueprint = Blueprint('auth', __name__)

@api_blueprint.route('/ping', methods=['GET'])
def ping():
    return jsonify({"message": "pong"}), 200

def validate_json_request():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON."}), 400
    try:
        return request.get_json()
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400

@auth_blueprint.route('/auth/register', methods=['POST'])
@swag_from({
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'username': {
                        'type': 'string',
                        'description': 'The username of the user.'
                    },
                    'password': {
                        'type': 'string',
                        'description': 'The password of the user.'
                    }
                },
                'required': ['username', 'password']
            },
            'description': 'JSON object containing username and password.'
        }
    ],
    'responses': {
        '201': {
            'description': 'User registered successfully',
            'examples': {
                'application/json': {
                    'message': 'User registered successfully!',
                    'user': {
                        'id': 'uuid',
                        'username': 'example_user'
                    }
                }
            }
        }
    }
})
def register_user():
    data = validate_json_request()
    if isinstance(data, tuple):
        return data  # Return error response if JSON is invalid

    hashed_password = generate_password_hash(data['password'],method='pbkdf2:sha256', salt_length=16)
    tenant_id = data.get('tenant_id') or str(uuid.uuid4())

    # First registered user gets Admin role; everyone else defaults to 'Sales'
    existing_users = list(users_container.query_items(
        query='SELECT VALUE COUNT(1) FROM c',
        enable_cross_partition_query=True
    ))
    default_role = 'Admin' if (not existing_users or existing_users[0] == 0) else 'Sales'

    user_id = str(uuid.uuid4())
    user = {
        'id': user_id,
        'userid': user_id,  # partition key field for Cosmos DB
        'tenant_id': tenant_id,
        'username': data['username'],
        'password': hashed_password,
        'role': data.get('role', default_role),
        'created_at': datetime.datetime.utcnow().isoformat()
    }
    users_container.create_item(body=user)
    return jsonify({
        "message": "User registered successfully!",
        "user": {
            "id": user['id'],
            "tenant_id": user['tenant_id'],
            "username": user['username'],
            "role": user['role']
        }
    }), 201

@auth_blueprint.route('/auth/login', methods=['POST'])
@swag_from({
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'username': {
                        'type': 'string',
                        'description': 'The username of the user.'
                    },
                    'password': {
                        'type': 'string',
                        'description': 'The password of the user.'
                    }
                },
                'required': ['username', 'password']
            },
            'description': 'JSON object containing username and password.'
        }
    ],
    'responses': {
        '200': {
            'description': 'Login successful',
            'examples': {
                'application/json': {
                    'message': 'Login successful!',
                    'user': {
                        'id': 'uuid',
                        'username': 'example_user'
                    },
                    'token': 'jwt_token'
                }
            }
        },
        '401': {
            'description': 'Invalid username or password',
            'examples': {
                'application/json': {
                    'message': 'Invalid username or password.'
                }
            }
        }
    }
})
def login_user():
    data = validate_json_request()
    if isinstance(data, tuple):
        return data  # Return error response if JSON is invalid

    query = "SELECT * FROM c WHERE c.username = @username"
    items = list(users_container.query_items(
        query=query,
        parameters=[{"name": "@username", "value": data['username']}],
        enable_cross_partition_query=True
    ))
    if items and check_password_hash(items[0]['password'], data['password']):
        jwt_secret = os.getenv("JWT_SECRET_KEY", os.getenv("SECRET_KEY", "your_secret_key"))
        tenant_id = items[0].get('tenant_id') or items[0].get('id')
        user_id = items[0]['id']
        access_token = jwt.encode(
            {
                "id": user_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
            },
            jwt_secret,
            algorithm="HS256"
        )

        # Generate and store refresh token (30-day expiry)
        refresh_token_value = secrets.token_urlsafe(48)
        refresh_token_expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        refresh_token_record = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "tenant_id": tenant_id,
            "token": refresh_token_value,
            "expires_at": refresh_token_expires.isoformat(),
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        refresh_tokens_container.create_item(body=refresh_token_record)

        return jsonify({
            "message": "Login successful!",
            "user": {
                "id": user_id,
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "role": items[0].get('role', 'Sales')
            },
            "token": access_token,
            "access_token": access_token,
            "refresh_token": refresh_token_value
        }), 200
    else:
        return jsonify({"message": "Invalid username or password."}), 401
 
@auth_blueprint.route('/auth/refresh', methods=['POST'])
def refresh_token():
    """
    Exchange a valid refresh token for a new access token.
    Input JSON: { "refresh_token": "<token>" }
    Returns: { "access_token": "<new_jwt>" }
    """
    data = request.get_json(silent=True) or {}
    incoming = data.get('refresh_token', '').strip()
    if not incoming:
        return jsonify({"error": "refresh_token is required"}), 400

    # Look up token in DB
    items = list(refresh_tokens_container.query_items(
        query="SELECT * FROM c WHERE c.token = @token",
        parameters=[{"name": "@token", "value": incoming}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({"error": "Invalid refresh token"}), 401

    record = items[0]

    # Check expiry
    expires_at = datetime.datetime.fromisoformat(record['expires_at'])
    if datetime.datetime.utcnow() > expires_at:
        # Expired — delete stale record
        try:
            refresh_tokens_container.delete_item(
                item=record['id'], partition_key=record['user_id']
            )
        except Exception:
            pass
        return jsonify({"error": "Refresh token expired"}), 401

    # Issue new access token
    jwt_secret = os.getenv("JWT_SECRET_KEY", os.getenv("SECRET_KEY", "your_secret_key"))

    # Fetch user to get current role/username
    user_items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": record['user_id']}],
        enable_cross_partition_query=True
    ))
    if not user_items:
        return jsonify({"error": "User not found"}), 401

    user = user_items[0]
    new_access_token = jwt.encode(
        {
            "id": record['user_id'],
            "user_id": record['user_id'],
            "tenant_id": record['tenant_id'],
            "username": user.get('username', ''),
            "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
        },
        jwt_secret,
        algorithm="HS256"
    )

    # Rotate refresh token (optional but more secure)
    new_refresh_value = secrets.token_urlsafe(48)
    new_expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    # Delete old record
    try:
        refresh_tokens_container.delete_item(
            item=record['id'], partition_key=record['user_id']
        )
    except Exception:
        pass
    # Create rotated record
    refresh_tokens_container.create_item(body={
        "id": str(uuid.uuid4()),
        "user_id": record['user_id'],
        "tenant_id": record['tenant_id'],
        "token": new_refresh_value,
        "expires_at": new_expires.isoformat(),
        "created_at": datetime.datetime.utcnow().isoformat()
    })

    return jsonify({
        "access_token": new_access_token,
        "token": new_access_token,
        "refresh_token": new_refresh_value
    }), 200


@auth_blueprint.route('/auth/logout', methods=['POST'])
def logout_user():
    """
    Logout endpoint. Revokes the provided refresh token from the DB.
    Input JSON (optional): { "refresh_token": "<token>" }
    """
    data = request.get_json(silent=True) or {}
    incoming = data.get('refresh_token', '').strip()
    if incoming:
        items = list(refresh_tokens_container.query_items(
            query="SELECT * FROM c WHERE c.token = @token",
            parameters=[{"name": "@token", "value": incoming}],
            enable_cross_partition_query=True
        ))
        for record in items:
            try:
                refresh_tokens_container.delete_item(
                    item=record['id'], partition_key=record['user_id']
                )
            except Exception:
                pass
    return jsonify({"message": "Logout successful."}), 200


@auth_blueprint.route('/auth/delete-account', methods=['DELETE'])
def delete_account():
    """
    Permanently delete the authenticated user's account and all associated data.
    Requires a valid Bearer token (validated by enforce_api_auth before_request).
    """
    from smart_invoice_pro.utils.cosmos_client import (
        invoices_container, customers_container, products_container,
        stock_container, bank_accounts_container, quotes_container,
        recurring_profiles_container, sales_orders_container,
        vendors_container, purchase_orders_container, bills_container,
        expenses_container, settings_container
    )

    user_id = request.user_id
    tenant_id = request.tenant_id

    def _bulk_delete(container, partition_key_field):
        """Query all items for this tenant and delete each one."""
        try:
            items = list(container.query_items(
                query="SELECT * FROM c WHERE c.tenant_id = @tid",
                parameters=[{"name": "@tid", "value": tenant_id}],
                enable_cross_partition_query=True
            ))
            for item in items:
                pk_val = item.get(partition_key_field) or item.get('id')
                try:
                    container.delete_item(item=item['id'], partition_key=pk_val)
                except Exception:
                    pass
        except Exception:
            pass

    _bulk_delete(invoices_container, 'customer_id')
    _bulk_delete(customers_container, 'customer_id')
    _bulk_delete(products_container, 'product_id')
    _bulk_delete(stock_container, 'product_id')
    _bulk_delete(bank_accounts_container, 'user_id')
    _bulk_delete(quotes_container, 'customer_id')
    _bulk_delete(recurring_profiles_container, 'customer_id')
    _bulk_delete(sales_orders_container, 'customer_id')
    _bulk_delete(vendors_container, 'vendor_id')
    _bulk_delete(purchase_orders_container, 'vendor_id')
    _bulk_delete(bills_container, 'vendor_id')
    _bulk_delete(expenses_container, 'id')
    _bulk_delete(settings_container, 'tenant_id')

    # Delete the user record (partition key field: userid)
    try:
        items = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": user_id}],
            enable_cross_partition_query=True
        ))
        if items:
            u = items[0]
            users_container.delete_item(
                item=u['id'],
                partition_key=u.get('userid', u['id'])
            )
    except Exception:
        pass

    return jsonify({"message": "Account deleted successfully."}), 200

from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container
import uuid
import os
from flasgger import swag_from
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import datetime

api_blueprint = Blueprint('api', __name__)
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
        token = jwt.encode(
            {
                "id": items[0]['id'],
                "user_id": items[0]['id'],
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            },
            jwt_secret,
            algorithm="HS256"
        )
        return jsonify({
            "message": "Login successful!",
            "user": {
                "id": items[0]['id'],
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "role": items[0].get('role', 'Sales')
            },
            "token": token
        }), 200
    else:
        return jsonify({"message": "Invalid username or password."}), 401
 
@auth_blueprint.route('/auth/logout', methods=['POST'])
def logout_user():
    """
    Logout endpoint. Clients should remove the token on their side.
    """
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

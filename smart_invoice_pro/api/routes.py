from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container
import uuid
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
    user = {
        'id': str(uuid.uuid4()),
        'username': data['username'],
        'password': hashed_password
    }
    users_container.create_item(body=user)
    return jsonify({"message": "User registered successfully!", "user": {"id": user['id'], "username": user['username']}}), 201

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

    query = f"SELECT * FROM c WHERE c.username = '{data['username']}'"
    items = list(users_container.query_items(query=query, enable_cross_partition_query=True))
    if items and check_password_hash(items[0]['password'], data['password']):
        token = jwt.encode(
            {
                "id": items[0]['id'],
                "username": items[0]['username'],
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            },
            "your_secret_key",  # Replace with your actual secret key
            algorithm="HS256"
        )
        return jsonify({
            "message": "Login successful!",
            "user": {"id": items[0]['id'], "username": items[0]['username']},
            "token": token
        }), 200
    else:
        return jsonify({"message": "Invalid username or password."}), 401

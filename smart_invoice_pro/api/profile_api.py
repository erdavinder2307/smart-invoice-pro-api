from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container
import uuid
from flasgger import swag_from
from datetime import datetime

profile_blueprint = Blueprint('profile', __name__)

def get_user_from_request():
    """Extract user info from request headers (following existing auth pattern)"""
    # In production, this would validate JWT token from Authorization header
    # For now, we'll get user_id from headers as done in other endpoints
    user_id = request.headers.get('X-User-Id')
    username = request.headers.get('X-Username')
    
    if not user_id:
        return None
    
    return {'id': user_id, 'username': username}

@profile_blueprint.route('/profile/me', methods=['GET'])
@swag_from({
    'tags': ['Profile'],
    'parameters': [
        {
            'name': 'X-User-Id',
            'in': 'header',
            'required': True,
            'type': 'string',
            'description': 'User ID from authentication'
        }
    ],
    'responses': {
        '200': {
            'description': 'User profile',
            'examples': {
                'application/json': {
                    'id': 'profile_uuid',
                    'user_id': 'user_uuid',
                    'name': 'John Doe',
                    'email': 'john@example.com',
                    'phone': '+1234567890',
                    'business_name': 'Acme Corp',
                    'gstin': '22AAAAA0000A1Z5',
                    'address': '123 Main St, City, State, ZIP',
                    'business_logo_url': '',
                    'default_currency': 'INR',
                    'date_format': 'DD/MM/YYYY',
                    'created_at': '2026-02-09T10:00:00Z',
                    'updated_at': '2026-02-09T10:00:00Z'
                }
            }
        },
        '401': {
            'description': 'Unauthorized',
            'examples': {'application/json': {'error': 'Unauthorized'}}
        }
    }
})
def get_profile():
    user = get_user_from_request()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = user['id']
    
    # Query for user profile
    query = f"SELECT * FROM c WHERE c.type = 'user_profile' AND c.user_id = '{user_id}'"
    items = list(users_container.query_items(query=query, enable_cross_partition_query=True))
    
    if items:
        # Return existing profile
        profile = items[0]
        # Remove sensitive fields
        safe_profile = {k: v for k, v in profile.items() if k not in ['password', '_rid', '_self', '_etag', '_attachments', '_ts']}
        return jsonify(safe_profile), 200
    else:
        # Return default profile if none exists
        default_profile = {
            'user_id': user_id,
            'name': user.get('username', ''),
            'email': '',
            'phone': '',
            'business_name': '',
            'gstin': '',
            'address': '',
            'business_logo_url': '',
            'default_currency': 'INR',
            'date_format': 'DD/MM/YYYY'
        }
        return jsonify(default_profile), 200

@profile_blueprint.route('/profile/update', methods=['POST'])
@swag_from({
    'tags': ['Profile'],
    'parameters': [
        {
            'name': 'X-User-Id',
            'in': 'header',
            'required': True,
            'type': 'string',
            'description': 'User ID from authentication'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'business_name': {'type': 'string'},
                    'gstin': {'type': 'string'},
                    'address': {'type': 'string'},
                    'business_logo_url': {'type': 'string'},
                    'default_currency': {'type': 'string'},
                    'date_format': {'type': 'string'}
                },
                'required': ['name', 'email']
            },
            'description': 'Profile data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile updated successfully',
            'examples': {
                'application/json': {
                    'message': 'Profile updated successfully',
                    'profile': {
                        'id': 'profile_uuid',
                        'user_id': 'user_uuid',
                        'name': 'John Doe',
                        'email': 'john@example.com',
                        'phone': '+1234567890',
                        'business_name': 'Acme Corp',
                        'gstin': '22AAAAA0000A1Z5',
                        'address': '123 Main St',
                        'default_currency': 'INR',
                        'date_format': 'DD/MM/YYYY',
                        'updated_at': '2026-02-09T10:00:00Z'
                    }
                }
            }
        },
        '400': {
            'description': 'Bad request',
            'examples': {'application/json': {'error': 'Name and email are required'}}
        },
        '401': {
            'description': 'Unauthorized',
            'examples': {'application/json': {'error': 'Unauthorized'}}
        }
    }
})
def update_profile():
    user = get_user_from_request()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    # Validate required fields
    if not data.get('name') or not data.get('email'):
        return jsonify({'error': 'Name and email are required'}), 400
    
    user_id = user['id']
    now = datetime.utcnow().isoformat()
    
    # Check if profile exists
    query = f"SELECT * FROM c WHERE c.type = 'user_profile' AND c.user_id = '{user_id}'"
    items = list(users_container.query_items(query=query, enable_cross_partition_query=True))
    
    if items:
        # Update existing profile
        profile = items[0]
        profile['name'] = data.get('name', profile.get('name', ''))
        profile['email'] = data.get('email', profile.get('email', ''))
        profile['phone'] = data.get('phone', profile.get('phone', ''))
        profile['business_name'] = data.get('business_name', profile.get('business_name', ''))
        profile['gstin'] = data.get('gstin', profile.get('gstin', ''))
        profile['address'] = data.get('address', profile.get('address', ''))
        profile['business_logo_url'] = data.get('business_logo_url', profile.get('business_logo_url', ''))
        profile['default_currency'] = data.get('default_currency', profile.get('default_currency', 'INR'))
        profile['date_format'] = data.get('date_format', profile.get('date_format', 'DD/MM/YYYY'))
        profile['updated_at'] = now
        
        # Use upsert for idempotent updates
        users_container.upsert_item(body=profile)
        
        # Remove internal Cosmos DB fields from response
        safe_profile = {k: v for k, v in profile.items() if k not in ['password', '_rid', '_self', '_etag', '_attachments', '_ts']}
        return jsonify({'message': 'Profile updated successfully', 'profile': safe_profile}), 200
    else:
        # Create new profile
        profile = {
            'id': f"profile_{str(uuid.uuid4())}",
            'userid': user_id,  # Partition key
            'type': 'user_profile',
            'user_id': user_id,
            'name': data.get('name'),
            'email': data.get('email'),
            'phone': data.get('phone', ''),
            'business_name': data.get('business_name', ''),
            'gstin': data.get('gstin', ''),
            'address': data.get('address', ''),
            'business_logo_url': data.get('business_logo_url', ''),
            'default_currency': data.get('default_currency', 'INR'),
            'date_format': data.get('date_format', 'DD/MM/YYYY'),
            'created_at': now,
            'updated_at': now
        }
        
        users_container.create_item(body=profile)
        
        # Remove internal Cosmos DB fields from response
        safe_profile = {k: v for k, v in profile.items() if k not in ['password', '_rid', '_self', '_etag', '_attachments', '_ts']}
        return jsonify({'message': 'Profile created successfully', 'profile': safe_profile}), 201

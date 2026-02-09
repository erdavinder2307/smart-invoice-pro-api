from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import bank_accounts_container
import uuid
from flasgger import swag_from
from datetime import datetime
import jwt
from functools import wraps

bank_accounts_blueprint = Blueprint('bank_accounts', __name__)

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            # Remove 'Bearer ' prefix if present
            if token.startswith('Bearer '):
                token = token[7:]
            
            data = jwt.decode(token, "your_secret_key", algorithms=["HS256"])
            current_user = data
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Token is invalid!'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated


@bank_accounts_blueprint.route('/bank-accounts', methods=['GET'])
@token_required
@swag_from({
    'tags': ['Bank Accounts'],
    'security': [{'Bearer': []}],
    'responses': {
        '200': {
            'description': 'List of bank accounts',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'user_id': 'user_uuid',
                        'bank_name': 'ICICI Bank',
                        'account_name': 'Business Current Account',
                        'account_type': 'current',
                        'sync_type': 'manual',
                        'last_imported_at': None,
                        'status': 'active',
                        'created_at': '2026-02-06T13:30:00Z',
                        'updated_at': '2026-02-06T13:30:00Z'
                    }
                ]
            }
        },
        '401': {
            'description': 'Unauthorized'
        }
    }
})
def get_bank_accounts(current_user):
    """
    Get all bank accounts for the authenticated user.
    """
    try:
        user_id = current_user.get('id')
        if not user_id:
            return jsonify({'message': 'Invalid user token'}), 401
        
        # Query bank accounts by user_id
        query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
        items = list(bank_accounts_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({'message': f'Error fetching bank accounts: {str(e)}'}), 500


@bank_accounts_blueprint.route('/bank-accounts', methods=['POST'])
@token_required
@swag_from({
    'tags': ['Bank Accounts'],
    'security': [{'Bearer': []}],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bank_name': {
                        'type': 'string',
                        'description': 'Name of the bank',
                        'example': 'ICICI Bank'
                    },
                    'account_name': {
                        'type': 'string',
                        'description': 'Name/label for the account',
                        'example': 'Business Current Account'
                    },
                    'account_type': {
                        'type': 'string',
                        'description': 'Type of account (savings, current, etc.)',
                        'example': 'current'
                    }
                },
                'required': ['bank_name', 'account_name', 'account_type']
            }
        }
    ],
    'responses': {
        '201': {
            'description': 'Bank account created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'user_id': 'user_uuid',
                    'bank_name': 'ICICI Bank',
                    'account_name': 'Business Current Account',
                    'account_type': 'current',
                    'sync_type': 'manual',
                    'last_imported_at': None,
                    'status': 'active',
                    'created_at': '2026-02-06T13:30:00Z',
                    'updated_at': '2026-02-06T13:30:00Z'
                }
            }
        },
        '400': {
            'description': 'Missing required fields'
        },
        '401': {
            'description': 'Unauthorized'
        }
    }
})
def create_bank_account(current_user):
    """
    Create a new manual bank account.
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['bank_name', 'account_name', 'account_type']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'message': f'Missing required field: {field}'}), 400
        
        user_id = current_user.get('id')
        if not user_id:
            return jsonify({'message': 'Invalid user token'}), 401
        
        # Create bank account object
        now = datetime.utcnow().isoformat() + 'Z'
        bank_account = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'bank_name': data['bank_name'],
            'account_name': data['account_name'],
            'account_type': data['account_type'],
            'sync_type': 'manual',
            'last_imported_at': None,
            'status': 'active',
            'created_at': now,
            'updated_at': now
        }
        
        # Store in Cosmos DB
        bank_accounts_container.create_item(body=bank_account)
        
        return jsonify(bank_account), 201
    except Exception as e:
        return jsonify({'message': f'Error creating bank account: {str(e)}'}), 500


@bank_accounts_blueprint.route('/bank-accounts/<account_id>', methods=['GET'])
@token_required
@swag_from({
    'tags': ['Bank Accounts'],
    'security': [{'Bearer': []}],
    'parameters': [
        {
            'name': 'account_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bank account ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bank account details',
            'examples': {
                'application/json': {
                    'account': {
                        'id': 'uuid',
                        'user_id': 'user_uuid',
                        'bank_name': 'ICICI Bank',
                        'account_name': 'Business Current Account',
                        'account_type': 'current',
                        'sync_type': 'manual',
                        'last_imported_at': None,
                        'status': 'active',
                        'created_at': '2026-02-06T13:30:00Z',
                        'updated_at': '2026-02-06T13:30:00Z'
                    },
                    'summary': {
                        'total_imports': 0,
                        'last_import_status': None
                    }
                }
            }
        },
        '401': {
            'description': 'Unauthorized'
        },
        '404': {
            'description': 'Bank account not found'
        }
    }
})
def get_bank_account(current_user, account_id):
    """
    Get bank account details with summary.
    """
    try:
        user_id = current_user.get('id')
        if not user_id:
            return jsonify({'message': 'Invalid user token'}), 401
        
        # Query specific bank account
        query = f"SELECT * FROM c WHERE c.id = '{account_id}' AND c.user_id = '{user_id}'"
        items = list(bank_accounts_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({'message': 'Bank account not found or access denied'}), 404
        
        account = items[0]
        
        # Add summary information
        summary = {
            'total_imports': 0,  # Placeholder for future import tracking
            'last_import_status': account.get('last_imported_at')
        }
        
        return jsonify({
            'account': account,
            'summary': summary
        }), 200
    except Exception as e:
        return jsonify({'message': f'Error fetching bank account: {str(e)}'}), 500

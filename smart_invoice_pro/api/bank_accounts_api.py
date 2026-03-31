from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import bank_accounts_container
import uuid
from flasgger import swag_from
from datetime import datetime

bank_accounts_blueprint = Blueprint('bank_accounts', __name__)

def get_user_from_request():
    """Extract user info from JWT token context set by auth middleware."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return None
    return {'id': user_id}


@bank_accounts_blueprint.route('/bank-accounts', methods=['GET'])
@swag_from({
    'tags': ['Bank Accounts'],
    'parameters': [
        {
            'name': 'X-User-Id',
            'in': 'header',
            'required': True,
            'type': 'string',
            'description': 'User ID'
        }
    ],
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
def get_bank_accounts():
    """
    Get all bank accounts for the authenticated user.
    """
    try:
        current_user = get_user_from_request()
        if not current_user:
            return jsonify({'message': 'X-User-Id header is required'}), 401
        user_id = current_user['id']
        
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
@swag_from({
    'tags': ['Bank Accounts'],
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
def create_bank_account():
    """
    Create a new manual bank account.
    """
    try:
        current_user = get_user_from_request()
        if not current_user:
            return jsonify({'message': 'X-User-Id header is required'}), 401
        user_id = current_user['id']

        data = request.get_json()
        
        # Validate required fields
        required_fields = ['bank_name', 'account_name', 'account_type']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'message': f'Missing required field: {field}'}), 400
        
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
@swag_from({
    'tags': ['Bank Accounts'],
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
def get_bank_account(account_id):
    """
    Get bank account details with summary.
    """
    try:
        current_user = get_user_from_request()
        if not current_user:
            return jsonify({'message': 'X-User-Id header is required'}), 401
        user_id = current_user['id']
        
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


@bank_accounts_blueprint.route('/bank-accounts/<account_id>', methods=['PUT'])
def update_bank_account(account_id):
    """
    Update a bank account's name, bank name, or account type.
    """
    try:
        current_user = get_user_from_request()
        if not current_user:
            return jsonify({'message': 'X-User-Id header is required'}), 401
        user_id = current_user['id']

        query = f"SELECT * FROM c WHERE c.id = '{account_id}' AND c.user_id = '{user_id}'"
        items = list(bank_accounts_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'message': 'Bank account not found or access denied'}), 404

        account = items[0]
        data = request.get_json() or {}

        updatable = ['bank_name', 'account_name', 'account_type']
        for field in updatable:
            if field in data:
                account[field] = data[field]

        account['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        bank_accounts_container.replace_item(item=account['id'], body=account)

        return jsonify(account), 200
    except Exception as e:
        return jsonify({'message': f'Error updating bank account: {str(e)}'}), 500


@bank_accounts_blueprint.route('/bank-accounts/<account_id>', methods=['DELETE'])
def delete_bank_account(account_id):
    """
    Delete a bank account.
    """
    try:
        current_user = get_user_from_request()
        if not current_user:
            return jsonify({'message': 'X-User-Id header is required'}), 401
        user_id = current_user['id']

        query = f"SELECT * FROM c WHERE c.id = '{account_id}' AND c.user_id = '{user_id}'"
        items = list(bank_accounts_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'message': 'Bank account not found or access denied'}), 404

        account = items[0]
        bank_accounts_container.delete_item(item=account['id'], partition_key=account['id'])

        return jsonify({'message': 'Bank account deleted successfully'}), 200
    except Exception as e:
        return jsonify({'message': f'Error deleting bank account: {str(e)}'}), 500

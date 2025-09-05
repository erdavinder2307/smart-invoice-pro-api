from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import customers_container
from smart_invoice_pro.utils.cosmos_client import invoices_container
import uuid
from flasgger import swag_from
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from functools import wraps

customers_blueprint = Blueprint('customers', __name__)

@customers_blueprint.route('/customers', methods=['POST'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
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
                    'address': {'type': 'string'},
                    'gst_number': {'type': 'string'},
                    'password': {'type': 'string', 'description': 'Optional password for customer login'}
                },
                'required': ['name', 'email']
            },
            'description': 'Customer data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Customer created',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'name': 'John Doe',
                    'email': 'john@example.com',
                    'phone': '1234567890',
                    'address': '123 Main St',
                    'gst_number': 'GST123',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        }
    }
})
def create_customer():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'customer_id': str(uuid.uuid4()),  # Unique customer ID
        'name': data['name'],
        'email': data['email'],
        'phone': data.get('phone', ''),
        'address': data.get('address', ''),
        'gst_number': data.get('gst_number', ''),
        'created_at': now,
        'updated_at': now
    }
    
    # Hash password if provided
    if 'password' in data and data['password']:
        item['password'] = generate_password_hash(data['password'], method='pbkdf2:sha256', salt_length=16)
    
    customers_container.create_item(body=item)
    # Remove password from response for security
    response_item = {k: v for k, v in item.items() if k != 'password'}
    return jsonify(response_item), 201

@customers_blueprint.route('/customers', methods=['GET'])
@swag_from({
    'tags': ['Customers'],
    'responses': {
        '200': {
            'description': 'List of all customers',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'name': 'John Doe',
                        'email': 'john@example.com',
                        'phone': '1234567890',
                        'address': '123 Main St',
                        'gst_number': 'GST123',
                        'created_at': '2025-06-05T12:00:00Z',
                        'updated_at': '2025-06-05T12:00:00Z'
                    }
                ]
            }
        }
    }
})
def list_customers():
    items = list(customers_container.read_all_items())
    # Remove password from all customer records for security
    safe_items = []
    for item in items:
        safe_item = {k: v for k, v in item.items() if k != 'password'}
        safe_items.append(safe_item)
    return jsonify(safe_items)

@customers_blueprint.route('/customers/<customer_id>', methods=['GET'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer details',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'name': 'John Doe',
                    'email': 'john@example.com',
                    'phone': '1234567890',
                    'address': '123 Main St',
                    'gst_number': 'GST123',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def get_customer(customer_id):
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    
    # Remove password from response for security
    customer = items[0]
    response_item = {k: v for k, v in customer.items() if k != 'password'}
    return jsonify(response_item)

@customers_blueprint.route('/customers/<customer_id>', methods=['PUT'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
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
                    'address': {'type': 'string'},
                    'gst_number': {'type': 'string'},
                    'password': {'type': 'string', 'description': 'Optional password for customer login'}
                }
            },
            'description': 'Customer data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer updated',
            'examples': {'application/json': {'id': 'uuid', 'name': 'John Doe', 'email': 'john@example.com', 'phone': '1234567890', 'address': '123 Main St', 'gst_number': 'GST123', 'created_at': '2025-06-05T12:00:00Z', 'updated_at': '2025-06-05T12:00:00Z'}}
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def update_customer(customer_id):
    data = request.get_json()
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    for field in ['name', 'email', 'phone', 'address', 'gst_number']:
        if field in data:
            item[field] = data[field]
    
    # Handle password update if provided
    if 'password' in data and data['password']:
        item['password'] = generate_password_hash(data['password'], method='pbkdf2:sha256', salt_length=16)
    
    item['updated_at'] = datetime.utcnow().isoformat()
    customers_container.replace_item(item=item['id'], body=item)
    
    # Remove password from response for security
    response_item = {k: v for k, v in item.items() if k != 'password'}
    return jsonify(response_item)

@customers_blueprint.route('/customers/<customer_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer deleted',
            'examples': {'application/json': {'message': 'Customer deleted'}}
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def delete_customer(customer_id):
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    # Cosmos DB partition key for customers is /customer_id, so use the value of 'customer_id' from the item
    customers_container.delete_item(item=item['id'], partition_key=item['customer_id'])
    return jsonify({'message': 'Customer deleted'})

@customers_blueprint.route('/customer/login', methods=['POST'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'email': {'type': 'string'},
                    'password': {'type': 'string'}
                },
                'required': ['email', 'password']
            },
            'description': 'Customer login credentials'
        }
    ],
    'responses': {
        '200': {
            'description': 'Login successful',
            'examples': {
                'application/json': {
                    'message': 'Login successful!',
                    'customer': {
                        'id': 'uuid',
                        'name': 'John Doe',
                        'email': 'john@example.com'
                    },
                    'token': 'jwt_token'
                }
            }
        },
        '401': {
            'description': 'Invalid email or password',
            'examples': {
                'application/json': {
                    'message': 'Invalid email or password.'
                }
            }
        }
    }
})
def customer_login():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    
    # Query customer by email
    query = f"SELECT * FROM c WHERE c.email = '{email}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    
    if not items:
        return jsonify({'message': 'Invalid email or password.'}), 401
    
    customer = items[0]
    
    # Check if customer has a password field (for existing customers without auth)
    if 'password' not in customer:
        return jsonify({'message': 'Account not set up for login. Please contact administrator.'}), 401
    
    # Verify password
    if check_password_hash(customer['password'], password):
        # Generate JWT token
        token = jwt.encode(
            {
                "id": customer['id'],
                "email": customer['email'],
                "name": customer['name'],
                "exp": datetime.utcnow() + timedelta(hours=24)
            },
            "customer_secret_key",  # Use a different secret for customer tokens
            algorithm="HS256"
        )
        
        return jsonify({
            "message": "Login successful!",
            "customer": {
                "id": customer['id'],
                "name": customer['name'],
                "email": customer['email']
            },
            "token": token
        }), 200
    else:
        return jsonify({"message": "Invalid email or password."}), 401

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
            
            data = jwt.decode(token, "customer_secret_key", algorithms=["HS256"])
            current_customer = data
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Token is invalid!'}), 401
        
        return f(current_customer, *args, **kwargs)
    return decorated

@customers_blueprint.route('/customer/invoices', methods=['GET'])
@token_required
@swag_from({
    'tags': ['Customer Invoices'],
    'parameters': [
        {
            'name': 'Authorization',
            'in': 'header',
            'required': True,
            'type': 'string',
            'description': 'Bearer JWT token'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of customer invoices',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'invoice_number': 'INV001',
                        'issue_date': '2025-08-22',
                        'due_date': '2025-09-22',
                        'total_amount': 1000.0,
                        'status': 'Issued'
                    }
                ]
            }
        },
        '401': {
            'description': 'Unauthorized'
        }
    }
})
def get_customer_invoices(current_customer):
    try:
        # Query invoices for the current customer by email
        query = f"SELECT * FROM c WHERE c.customer_email = '{current_customer['email']}'"
        items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
        
        # Format invoices for frontend display
        formatted_invoices = []
        for invoice in items:
            formatted_invoices.append({
                'id': invoice.get('id'),
                'invoice_number': invoice.get('invoice_number'),
                'issue_date': invoice.get('issue_date'),
                'due_date': invoice.get('due_date'),
                'total_amount': invoice.get('total_amount', 0),
                'status': invoice.get('status', 'Draft'),
                'customer_name': invoice.get('customer_name'),
                'created_at': invoice.get('created_at'),
                'updated_at': invoice.get('updated_at')
            })
        
        return jsonify(formatted_invoices), 200
    except Exception as e:
        return jsonify({'error': 'Could not fetch invoices', 'details': str(e)}), 500

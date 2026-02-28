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
                    'display_name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'customer_type': {'type': 'string', 'enum': ['business', 'individual']},
                    'salutation': {'type': 'string'},
                    'first_name': {'type': 'string'},
                    'last_name': {'type': 'string'},
                    'company_name': {'type': 'string'},
                    'language': {'type': 'string'},
                    'gst_treatment': {'type': 'string', 'enum': ['regular', 'composition', 'unregistered']},
                    'place_of_supply': {'type': 'string'},
                    'gst_number': {'type': 'string'},
                    'pan': {'type': 'string'},
                    'tax_preference': {'type': 'string', 'enum': ['yes', 'no']},
                    'currency': {'type': 'string'},
                    'opening_balance': {'type': 'number'},
                    'payment_terms': {'type': 'string'},
                    'billing_address': {'type': 'string'},
                    'billing_city': {'type': 'string'},
                    'billing_state': {'type': 'string'},
                    'billing_zip': {'type': 'string'},
                    'billing_country': {'type': 'string'},
                    'shipping_address': {'type': 'string'},
                    'shipping_city': {'type': 'string'},
                    'shipping_state': {'type': 'string'},
                    'shipping_zip': {'type': 'string'},
                    'shipping_country': {'type': 'string'},
                    'portal_enabled': {'type': 'boolean'},
                    'portal_password': {'type': 'string', 'description': 'Optional password for customer portal login'},
                    'remarks': {'type': 'string'}
                },
                'required': ['display_name', 'email', 'phone']
            },
            'description': 'Customer data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Customer created',
            'schema': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'customer_id': {'type': 'string'},
                    'display_name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'created_at': {'type': 'string'},
                    'updated_at': {'type': 'string'}
                }
            }
        },
        '400': {
            'description': 'Invalid input data'
        }
    }
})
def create_customer():
    data = request.get_json()
    
    # Validate required fields
    if not data.get('display_name'):
        return jsonify({'error': 'Display name is required'}), 400
    if not data.get('email'):
        return jsonify({'error': 'Email is required'}), 400
    if not data.get('phone'):
        return jsonify({'error': 'Phone is required'}), 400
    
    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'customer_id': str(uuid.uuid4()),
        'display_name': data['display_name'],
        'email': data['email'],
        'phone': data['phone'],
        'customer_type': data.get('customer_type', 'business'),
        'salutation': data.get('salutation', 'Mr'),
        'first_name': data.get('first_name', ''),
        'last_name': data.get('last_name', ''),
        'company_name': data.get('company_name', ''),
        'language': data.get('language', 'en'),
        'gst_treatment': data.get('gst_treatment', 'regular'),
        'place_of_supply': data.get('place_of_supply', ''),
        'gst_number': data.get('gst_number', ''),
        'pan': data.get('pan', ''),
        'tax_preference': data.get('tax_preference', 'yes'),
        'currency': data.get('currency', 'INR'),
        'opening_balance': float(data.get('opening_balance', 0)),
        'payment_terms': data.get('payment_terms', 'Net 30'),
        'billing_address': data.get('billing_address', ''),
        'billing_city': data.get('billing_city', ''),
        'billing_state': data.get('billing_state', ''),
        'billing_zip': data.get('billing_zip', ''),
        'billing_country': data.get('billing_country', 'India'),
        'shipping_address': data.get('shipping_address', ''),
        'shipping_city': data.get('shipping_city', ''),
        'shipping_state': data.get('shipping_state', ''),
        'shipping_zip': data.get('shipping_zip', ''),
        'shipping_country': data.get('shipping_country', 'India'),
        'portal_enabled': data.get('portal_enabled', False),
        'remarks': data.get('remarks', ''),
        'created_at': now,
        'updated_at': now
    }
    
    # Hash portal password if provided
    if data.get('portal_enabled') and data.get('portal_password'):
        item['portal_password'] = generate_password_hash(data['portal_password'], method='pbkdf2:sha256', salt_length=16)
    
    # For backward compatibility, also set 'name' field
    item['name'] = item['display_name']
    item['address'] = item['billing_address']
    
    customers_container.create_item(body=item)
    # Remove password from response for security
    response_item = {k: v for k, v in item.items() if k != 'portal_password'}
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
                    'display_name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'customer_type': {'type': 'string'},
                    'salutation': {'type': 'string'},
                    'first_name': {'type': 'string'},
                    'last_name': {'type': 'string'},
                    'company_name': {'type': 'string'},
                    'language': {'type': 'string'},
                    'gst_treatment': {'type': 'string'},
                    'place_of_supply': {'type': 'string'},
                    'gst_number': {'type': 'string'},
                    'pan': {'type': 'string'},
                    'tax_preference': {'type': 'string'},
                    'currency': {'type': 'string'},
                    'opening_balance': {'type': 'number'},
                    'payment_terms': {'type': 'string'},
                    'billing_address': {'type': 'string'},
                    'billing_city': {'type': 'string'},
                    'billing_state': {'type': 'string'},
                    'billing_zip': {'type': 'string'},
                    'billing_country': {'type': 'string'},
                    'shipping_address': {'type': 'string'},
                    'shipping_city': {'type': 'string'},
                    'shipping_state': {'type': 'string'},
                    'shipping_zip': {'type': 'string'},
                    'shipping_country': {'type': 'string'},
                    'portal_enabled': {'type': 'boolean'},
                    'portal_password': {'type': 'string'},
                    'remarks': {'type': 'string'}
                }
            },
            'description': 'Customer data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer updated',
            'schema': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'display_name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'updated_at': {'type': 'string'}
                }
            }
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
    
    # List of updateable fields (both old and new)
    updateable_fields = [
        'display_name', 'email', 'phone', 'customer_type', 'salutation', 'first_name', 'last_name',
        'company_name', 'language', 'gst_treatment', 'place_of_supply', 'gst_number', 'pan',
        'tax_preference', 'currency', 'opening_balance', 'payment_terms',
        'billing_address', 'billing_city', 'billing_state', 'billing_zip', 'billing_country',
        'shipping_address', 'shipping_city', 'shipping_state', 'shipping_zip', 'shipping_country',
        'portal_enabled', 'remarks'
    ]
    
    # Update each field if provided in request
    for field in updateable_fields:
        if field in data:
            if field == 'opening_balance':
                item[field] = float(data[field])
            else:
                item[field] = data[field]
    
    # Update backward compatibility fields
    if 'display_name' in data:
        item['name'] = data['display_name']
    if 'billing_address' in data:
        item['address'] = data['billing_address']
    
    # Handle portal password update if provided
    if data.get('portal_enabled') and data.get('portal_password'):
        item['portal_password'] = generate_password_hash(data['portal_password'], method='pbkdf2:sha256', salt_length=16)
    
    item['updated_at'] = datetime.utcnow().isoformat()
    customers_container.replace_item(item=item['id'], body=item)
    
    # Remove password from response for security
    response_item = {k: v for k, v in item.items() if k != 'portal_password'}
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

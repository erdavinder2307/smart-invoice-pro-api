from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import invoices_container
import uuid
from flasgger import swag_from
from datetime import datetime
from enum import Enum
import jwt
from functools import wraps

api_blueprint = Blueprint('api', __name__)

class InvoiceStatus(Enum):
    Draft = 'Draft'
    Issued = 'Issued'
    Paid = 'Paid'
    Overdue = 'Overdue'
    Cancelled = 'Cancelled'

def validate_invoice_patch(data):
    allowed_fields = {
        'invoice_number': str,
        'customer_id': int,
        'issue_date': str,
        'due_date': str,
        'payment_terms': str,
        'subtotal': float,
        'cgst_amount': float,
        'sgst_amount': float,
        'igst_amount': float,
        'total_tax': float,
        'total_amount': float,
        'amount_paid': float,
        'balance_due': float,
        'status': str,
        'payment_mode': str,
        'notes': str,
        'terms_conditions': str,
        'is_gst_applicable': bool,
        'invoice_type': str,
        'created_at': str,
        'updated_at': str
    }
    errors = {}
    for k, v in data.items():
        if k not in allowed_fields:
            errors[k] = 'Unknown field'
            continue
        if k == 'status' and v not in InvoiceStatus._value2member_map_:
            errors[k] = f'Invalid status: {v}'
        # Optionally add more type checks here
    return errors

@api_blueprint.route('/invoices', methods=['POST'])
@swag_from({
    'tags': ['Invoices'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'invoice_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'issue_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Issued', 'Paid', 'Overdue', 'Cancelled']},
                    'payment_mode': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'invoice_type': {'type': 'string'},
                    'created_at': {'type': 'string', 'format': 'date-time'},
                    'updated_at': {'type': 'string', 'format': 'date-time'}
                },
                'required': ['invoice_number', 'customer_id', 'issue_date', 'due_date', 'subtotal', 'total_amount', 'status']
            },
            'description': 'Invoice data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Invoice created',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'invoice_number': 'INV-001',
                    'customer_id': 123,
                    'issue_date': '2025-06-05',
                    'due_date': '2025-06-20',
                    'payment_terms': 'Net 15',
                    'subtotal': 1000.0,
                    'cgst_amount': 90.0,
                    'sgst_amount': 90.0,
                    'igst_amount': 0.0,
                    'total_tax': 180.0,
                    'total_amount': 1180.0,
                    'amount_paid': 0.0,
                    'balance_due': 1180.0,
                    'status': 'Draft',
                    'payment_mode': 'Bank Transfer',
                    'notes': 'Thank you!',
                    'terms_conditions': 'Payment due in 15 days.',
                    'is_gst_applicable': True,
                    'invoice_type': 'Standard',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        }
    }
})
def create_invoice():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'invoice_number': data['invoice_number'],
        'customer_id': data['customer_id'],
        'customer_name': data.get('customer_name', ''),
        'customer_email': data.get('customer_email', ''),
        'customer_phone': data.get('customer_phone', ''),
        'issue_date': data['issue_date'],
        'due_date': data['due_date'],
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data['subtotal'],
        'cgst_amount': data.get('cgst_amount', 0.0),
        'sgst_amount': data.get('sgst_amount', 0.0),
        'igst_amount': data.get('igst_amount', 0.0),
        'total_tax': data.get('total_tax', 0.0),
        'total_amount': data['total_amount'],
        'amount_paid': data.get('amount_paid', 0.0),
        'balance_due': data.get('balance_due', data['total_amount']),
        'status': data['status'],
        'payment_mode': data.get('payment_mode', ''),
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'is_gst_applicable': data.get('is_gst_applicable', False),
        'invoice_type': data.get('invoice_type', ''),
        'created_at': data.get('created_at', now),
        'updated_at': data.get('updated_at', now)
    }
    invoices_container.create_item(body=item)
    return jsonify(item), 201

@api_blueprint.route('/invoices', methods=['GET'])
@swag_from({
    'tags': ['Invoices'],
    'responses': {
        '200': {
            'description': 'List of all invoices',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'invoice_number': 'INV-001',
                        'customer_id': 123,
                        'issue_date': '2025-06-05',
                        'due_date': '2025-06-20',
                        'payment_terms': 'Net 15',
                        'subtotal': 1000.0,
                        'cgst_amount': 90.0,
                        'sgst_amount': 90.0,
                        'igst_amount': 0.0,
                        'total_tax': 180.0,
                        'total_amount': 1180.0,
                        'amount_paid': 0.0,
                        'balance_due': 1180.0,
                        'status': 'Draft',
                        'payment_mode': 'Bank Transfer',
                        'notes': 'Thank you!',
                        'terms_conditions': 'Payment due in 15 days.',
                        'is_gst_applicable': True,
                        'invoice_type': 'Standard',
                        'created_at': '2025-06-05T12:00:00Z',
                        'updated_at': '2025-06-05T12:00:00Z'
                    }
                ]
            }
        }
    }
})
def list_invoices():
    items = list(invoices_container.read_all_items())
    return jsonify(items)

# @api_blueprint.route('/invoices/<customer_id>', methods=['GET'])
# @swag_from({
#     'tags': ['Invoices'],
#     'parameters': [
#         {
#             'name': 'customer_id',
#             'in': 'path',
#             'type': 'integer',
#             'required': True,
#             'description': 'Customer ID'
#         }
#     ],
#     'responses': {
#         '200': {
#             'description': 'Invoices for a customer',
#             'examples': {
#                 'application/json': [
#                     {
#                         'id': 'uuid',
#                         'invoice_number': 'INV-001',
#                         'customer_id': 123,
#                         'issue_date': '2025-06-05',
#                         'due_date': '2025-06-20',
#                         'payment_terms': 'Net 15',
#                         'subtotal': 1000.0,
#                         'cgst_amount': 90.0,
#                         'sgst_amount': 90.0,
#                         'igst_amount': 0.0,
#                         'total_tax': 180.0,
#                         'total_amount': 1180.0,
#                         'amount_paid': 0.0,
#                         'balance_due': 1180.0,
#                         'status': 'Draft',
#                         'payment_mode': 'Bank Transfer',
#                         'notes': 'Thank you!',
#                         'terms_conditions': 'Payment due in 15 days.',
#                         'is_gst_applicable': True,
#                         'invoice_type': 'Standard',
#                         'created_at': '2025-06-05T12:00:00Z',
#                         'updated_at': '2025-06-05T12:00:00Z'
#                     }
#                 ]
#             }
#         }
#     }
# })
# def get_invoices(customer_id):
#     query = f"SELECT * FROM c WHERE c.customer_id = {customer_id}"
#     items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
#     return jsonify(items)

@api_blueprint.route('/invoices/<invoice_id>', methods=['GET'])
@swag_from({
    'tags': ['Invoices'],
    'parameters': [
        {
            'name': 'invoice_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Invoice ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Invoice details',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'invoice_number': 'INV-001',
                    'customer_id': 123,
                    'issue_date': '2025-06-05',
                    'due_date': '2025-06-20',
                    'payment_terms': 'Net 15',
                    'subtotal': 1000.0,
                    'cgst_amount': 90.0,
                    'sgst_amount': 90.0,
                    'igst_amount': 0.0,
                    'total_tax': 180.0,
                    'total_amount': 1180.0,
                    'amount_paid': 0.0,
                    'balance_due': 1180.0,
                    'status': 'Draft',
                    'payment_mode': 'Bank Transfer',
                    'notes': 'Thank you!',
                    'terms_conditions': 'Payment due in 15 days.',
                    'is_gst_applicable': True,
                    'invoice_type': 'Standard',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        },
        '404': {
            'description': 'Invoice not found',
            'examples': {'application/json': {'error': 'Invoice not found'}}
        }
    }
})
def get_invoice(invoice_id):
    query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
    items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    return jsonify(items[0])

@api_blueprint.route('/invoices/<invoice_id>', methods=['PUT'])
@swag_from({
    'tags': ['Invoices'],
    'parameters': [
        {
            'name': 'invoice_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Invoice ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'invoice_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'issue_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Issued', 'Paid', 'Overdue', 'Cancelled']},
                    'payment_mode': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'invoice_type': {'type': 'string'},
                    'created_at': {'type': 'string', 'format': 'date-time'},
                    'updated_at': {'type': 'string', 'format': 'date-time'}
                },
                'required': ['invoice_number', 'customer_id', 'issue_date', 'due_date', 'subtotal', 'total_amount', 'status']
            },
            'description': 'Full invoice data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Invoice updated',
            'examples': {'application/json': {'id': 'uuid', 'invoice_number': 'INV-001', 'customer_id': 123, 'status': 'Paid', 'updated_at': '2025-06-05T12:00:00Z'}}
        },
        '404': {
            'description': 'Invoice not found',
            'examples': {'application/json': {'error': 'Invoice not found'}}
        }
    }
})
def update_invoice(invoice_id):
    data = request.get_json()
    query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
    items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    # Update all fields from the request (PUT = full replacement)
    for field in [
        'invoice_number', 'customer_id', 'issue_date', 'due_date', 'payment_terms',
        'subtotal', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_tax',
        'total_amount', 'amount_paid', 'balance_due', 'status', 'payment_mode',
        'notes', 'terms_conditions', 'is_gst_applicable', 'invoice_type',
        'created_at', 'updated_at'
    ]:
        if field in data:
            item[field] = data[field]
    item['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.replace_item(item=item['id'], body=item)
    return jsonify(item)

@api_blueprint.route('/invoices/<invoice_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Invoices'],
    'parameters': [
        {
            'name': 'invoice_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Invoice ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Invoice deleted',
            'examples': {'application/json': {'message': 'Invoice deleted'}}
        },
        '404': {
            'description': 'Invoice not found',
            'examples': {'application/json': {'error': 'Invoice not found'}}
        }
    }
})
def delete_invoice(invoice_id):
    query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
    items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    invoices_container.delete_item(item=item['id'], partition_key=item['customer_id'])
    return jsonify({'message': 'Invoice deleted'})

@api_blueprint.route('/invoices/<invoice_id>', methods=['PATCH'])
@swag_from({
    'tags': ['Invoices'],
    'parameters': [
        {
            'name': 'invoice_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Invoice ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'invoice_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'issue_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Issued', 'Paid', 'Overdue', 'Cancelled']},
                    'payment_mode': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'invoice_type': {'type': 'string'},
                    'created_at': {'type': 'string', 'format': 'date-time'},
                    'updated_at': {'type': 'string', 'format': 'date-time'}
                },
                'description': 'Partial invoice fields to update.'
            }
        }
    ],
    'responses': {
        '200': {
            'description': 'Invoice updated',
            'examples': {
                'application/json': {
                    'message': 'Invoice updated',
                    'invoice': {
                        'id': 'uuid',
                        'invoice_number': 'INV-001',
                        'customer_id': 123,
                        'status': 'Paid',
                        'updated_at': '2025-06-05T12:00:00Z'
                    }
                }
            }
        },
        '400': {
            'description': 'Validation failed',
            'examples': {'application/json': {'error': 'Validation failed', 'details': {'field': 'reason'}}}
        },
        '404': {
            'description': 'Invoice not found',
            'examples': {'application/json': {'error': 'Invoice not found'}}
        }
    }
})
def patch_invoice(invoice_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    errors = validate_invoice_patch(data)
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400
    query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
    items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    for k, v in data.items():
        item[k] = v
    item['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.replace_item(item=item['id'], body=item)
    return jsonify({'message': 'Invoice updated', 'invoice': item})

@api_blueprint.route('/invoices/next-number', methods=['GET'])
@swag_from({
    'tags': ['Invoices'],
    'responses': {
        '200': {
            'description': 'Next available invoice number',
            'examples': {
                'application/json': {'next_invoice_number': 'INV-006'}
            }
        },
        '500': {
            'description': 'Error occurred',
            'examples': {'application/json': {'error': 'Could not determine next invoice number'}}
        }
    }
})
def get_next_invoice_number():
    try:
        items = list(invoices_container.read_all_items())
        max_num = 0
        prefix = 'INV-'
        for item in items:
            inv_num = item.get('invoice_number', '')
            if inv_num.startswith(prefix):
                try:
                    num = int(inv_num.replace(prefix, ''))
                    if num > max_num:
                        max_num = num
                except Exception:
                    continue
        next_num = max_num + 1
        next_invoice_number = f"{prefix}{next_num:03d}"
        return jsonify({'next_invoice_number': next_invoice_number})
    except Exception as e:
        return jsonify({'error': 'Could not determine next invoice number', 'details': str(e)}), 500

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

@api_blueprint.route('/customer/invoices', methods=['GET'])
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
        # Assuming invoices have customer_email field
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

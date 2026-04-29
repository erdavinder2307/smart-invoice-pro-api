from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import bills_container, get_container
import uuid
from flasgger import swag_from
from datetime import datetime
from enum import Enum

bills_blueprint = Blueprint('bills', __name__)

class PaymentStatus(Enum):
    Unpaid = 'Unpaid'
    PartiallyPaid = 'Partially Paid'
    Paid = 'Paid'
    Overdue = 'Overdue'

def validate_bill_data(data, is_update=False):
    """Validate bill data"""
    errors = {}
    
    if not is_update:
        required_fields = ['bill_number', 'vendor_id', 'bill_date', 'due_date', 'total_amount']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate payment status
    if 'payment_status' in data and data['payment_status'] not in PaymentStatus._value2member_map_:
        errors['payment_status'] = f'Invalid payment status: {data["payment_status"]}'
    
    # Validate dates
    if 'bill_date' in data and 'due_date' in data:
        try:
            bill = datetime.fromisoformat(data['bill_date'].replace('Z', '+00:00'))
            due = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
            if due < bill:
                errors['due_date'] = 'Due date cannot be before bill date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    return errors

@bills_blueprint.route('/bills', methods=['POST'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'bill_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'payment_status': {'type': 'string', 'enum': ['Unpaid', 'Partially Paid', 'Paid', 'Overdue']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'items': {'type': 'array'},
                    'expenses': {'type': 'array'},
                    'converted_from_po_id': {'type': 'string'}
                },
                'required': ['bill_number', 'vendor_id', 'bill_date', 'due_date', 'total_amount']
            },
            'description': 'Bill data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Bill created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'bill_number': 'BILL-001',
                    'vendor_id': '123',
                    'payment_status': 'Unpaid'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_bill():
    """Create a new bill"""
    data = request.get_json()
    
    # Validate data
    errors = validate_bill_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    item = {
        'id': str(uuid.uuid4()),
        'bill_number': data['bill_number'],
        'vendor_id': data['vendor_id'],
        'vendor_name': data.get('vendor_name', ''),
        'bill_date': data['bill_date'],
        'due_date': data['due_date'],
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data.get('subtotal', 0.0),
        'tax_amount': data.get('tax_amount', 0.0),
        'total_amount': data['total_amount'],
        'amount_paid': data.get('amount_paid', 0.0),
        'balance_due': data.get('balance_due', data['total_amount']),
        'payment_status': data.get('payment_status', 'Unpaid'),
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'items': data.get('items', []),
        'expenses': data.get('expenses', []),
        'converted_from_po_id': data.get('converted_from_po_id', None),
        'payment_history': [],  # Track payment records
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = bills_container.create_item(body=item)
        
        # Increment stock for each item in the bill
        stock_container = get_container("stock", "/product_id")
        for bill_item in data.get('items', []):
            if 'product_id' in bill_item and 'quantity' in bill_item:
                try:
                    stock_transaction = {
                        'id': str(uuid.uuid4()),
                        'product_id': str(bill_item['product_id']),
                        'quantity': float(bill_item['quantity']),
                        'type': 'IN',
                        'source': f'Bill {data["bill_number"]}',
                        'reference_id': item['id'],
                        'timestamp': now
                    }
                    stock_container.create_item(body=stock_transaction)
                except Exception as e:
                    print(f"Error updating stock for product {bill_item.get('product_id')}: {str(e)}")
        
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create bill: {str(e)}"}), 500

@bills_blueprint.route('/bills', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'payment_status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by payment status'
        },
        {
            'name': 'vendor_id',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by vendor ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of bills',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_bills():
    """Get all bills with optional filters"""
    try:
        status_filter = request.args.get('payment_status')
        vendor_id_filter = request.args.get('vendor_id')

        _ALLOWED_SORT_FIELDS = {'created_at', 'bill_number', 'bill_date', 'due_date', 'total_amount'}
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'created_at'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        parameters = [{"name": "@tenant_id", "value": request.tenant_id}]

        if status_filter:
            query += " AND c.payment_status = @payment_status"
            parameters.append({"name": "@payment_status", "value": status_filter})
        if vendor_id_filter:
            query += " AND c.vendor_id = @vendor_id"
            parameters.append({"name": "@vendor_id", "value": vendor_id_filter})

        query += f" ORDER BY c.{sort_by} {sort_order}"

        items = list(bills_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve bills: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill retrieved successfully'
        },
        '404': {
            'description': 'Bill not found'
        }
    }
})
def get_bill(bill_id):
    """Get a bill by ID"""
    try:
        query = f"SELECT * FROM c WHERE c.id = '{bill_id}'"
        items = list(bills_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve bill: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>', methods=['PUT'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'bill_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'payment_status': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'items': {'type': 'array'},
                    'expenses': {'type': 'array'}
                }
            },
            'description': 'Updated bill data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill updated successfully'
        },
        '404': {
            'description': 'Bill not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def update_bill(bill_id):
    """Update a bill"""
    data = request.get_json()
    
    # Validate data
    errors = validate_bill_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing bill
        query = f"SELECT * FROM c WHERE c.id = '{bill_id}'"
        items = list(bills_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        bill = items[0]
        
        # Update fields
        updatable_fields = [
            'bill_number', 'vendor_id', 'vendor_name', 'bill_date', 'due_date',
            'payment_terms', 'subtotal', 'tax_amount', 'total_amount', 'amount_paid',
            'balance_due', 'payment_status', 'notes', 'terms_conditions', 'items', 'expenses'
        ]
        
        for field in updatable_fields:
            if field in data:
                bill[field] = data[field]
        
        bill['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = bills_container.replace_item(
            item=bill['id'],
            body=bill
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update bill: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill deleted successfully'
        },
        '404': {
            'description': 'Bill not found'
        }
    }
})
def delete_bill(bill_id):
    """Delete a bill"""
    try:
        # Fetch the bill to get partition key
        query = f"SELECT * FROM c WHERE c.id = '{bill_id}'"
        items = list(bills_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        bill = items[0]
        
        # Check if bill has been paid
        if bill.get('payment_status') == 'Paid':
            return jsonify({"error": "Cannot delete a bill that has been paid"}), 400
        
        bills_container.delete_item(
            item=bill['id'],
            partition_key=bill['vendor_id']
        )
        
        return jsonify({"message": "Bill deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete bill: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>/record-payment', methods=['POST'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'amount': {'type': 'number'},
                    'payment_date': {'type': 'string', 'format': 'date'},
                    'payment_method': {'type': 'string'},
                    'reference': {'type': 'string'},
                    'notes': {'type': 'string'}
                },
                'required': ['amount', 'payment_date']
            },
            'description': 'Payment record'
        }
    ],
    'responses': {
        '200': {
            'description': 'Payment recorded successfully',
            'examples': {
                'application/json': {
                    'message': 'Payment recorded successfully',
                    'payment_status': 'Paid',
                    'balance_due': 0
                }
            }
        },
        '400': {
            'description': 'Invalid payment amount or bill not found'
        }
    }
})
def record_payment(bill_id):
    """Record a payment against a bill"""
    data = request.get_json()
    amount = data.get('amount', 0)
    
    if amount <= 0:
        return jsonify({"error": "Payment amount must be greater than zero"}), 400
    
    try:
        # Fetch the bill
        query = f"SELECT * FROM c WHERE c.id = '{bill_id}'"
        items = list(bills_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        bill = items[0]
        
        # Validate payment amount
        if amount > bill['balance_due']:
            return jsonify({"error": "Payment amount exceeds balance due"}), 400
        
        # Record payment
        payment_record = {
            'id': str(uuid.uuid4()),
            'amount': amount,
            'payment_date': data.get('payment_date'),
            'payment_method': data.get('payment_method', ''),
            'reference': data.get('reference', ''),
            'notes': data.get('notes', ''),
            'recorded_at': datetime.utcnow().isoformat()
        }
        
        if 'payment_history' not in bill:
            bill['payment_history'] = []
        
        bill['payment_history'].append(payment_record)
        
        # Update payment amounts
        bill['amount_paid'] = bill.get('amount_paid', 0) + amount
        bill['balance_due'] = bill['total_amount'] - bill['amount_paid']
        
        # Update payment status
        if bill['balance_due'] <= 0:
            bill['payment_status'] = 'Paid'
        elif bill['amount_paid'] > 0:
            bill['payment_status'] = 'Partially Paid'
        else:
            bill['payment_status'] = 'Unpaid'
        
        bill['updated_at'] = datetime.utcnow().isoformat()
        
        updated_bill = bills_container.replace_item(
            item=bill['id'],
            body=bill
        )
        
        return jsonify({
            "message": "Payment recorded successfully",
            "payment_status": updated_bill['payment_status'],
            "amount": amount,
            "balance_due": updated_bill['balance_due']
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to record payment: {str(e)}"}), 500

@bills_blueprint.route('/bills/next-number', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'responses': {
        '200': {
            'description': 'Next available bill number',
            'examples': {
                'application/json': {
                    'next_number': 'BILL-001'
                }
            }
        }
    }
})
def get_next_bill_number():
    """Get the next available bill number"""
    try:
        query = "SELECT * FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(bills_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "BILL-001"}), 200
        
        last_bill = items[0]
        last_number = last_bill.get('bill_number', 'BILL-000')
        
        # Extract number part (assuming format BILL-XXX)
        try:
            prefix, num_str = last_number.rsplit('-', 1)
            next_num = int(num_str) + 1
            next_number = f"{prefix}-{next_num:03d}"
        except:
            next_number = "BILL-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate next bill number: {str(e)}"}), 500

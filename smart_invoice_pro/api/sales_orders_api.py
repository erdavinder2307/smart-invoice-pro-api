from flask import Blueprint, request, jsonify, g, make_response
from smart_invoice_pro.utils.cosmos_client import sales_orders_container, invoices_container
from smart_invoice_pro.api.auth_middleware import token_required
import uuid
import base64
from flasgger import swag_from
from datetime import datetime
from enum import Enum
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding

sales_orders_blueprint = Blueprint('sales_orders', __name__)

class SalesOrderStatus(Enum):
    Draft = 'Draft'
    Confirmed = 'Confirmed'
    Closed = 'Closed'
    Invoiced = 'Invoiced'
    Cancelled = 'Cancelled'

def validate_sales_order_data(data, is_update=False):
    """Validate sales order data"""
    errors = {}
    
    if not is_update:
        required_fields = ['so_number', 'customer_id', 'order_date', 'total_amount', 'status']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate status
    if 'status' in data and data['status'] not in SalesOrderStatus._value2member_map_:
        errors['status'] = f'Invalid status: {data["status"]}'
    
    # Validate dates
    if 'order_date' in data and 'delivery_date' in data and data['delivery_date']:
        try:
            order = datetime.fromisoformat(data['order_date'].replace('Z', '+00:00'))
            delivery = datetime.fromisoformat(data['delivery_date'].replace('Z', '+00:00'))
            if delivery < order:
                errors['delivery_date'] = 'Delivery date cannot be before order date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    return errors

@sales_orders_blueprint.route('/sales-orders', methods=['POST'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'so_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'customer_name': {'type': 'string'},
                    'customer_email': {'type': 'string'},
                    'customer_phone': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Confirmed', 'Closed', 'Invoiced', 'Cancelled']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'subject': {'type': 'string'},
                    'salesperson': {'type': 'string'},
                    'items': {'type': 'array'},
                    'converted_to_invoice_id': {'type': 'string'},
                    'converted_to_po_id': {'type': 'string'},
                    'converted_from_quote_id': {'type': 'string'}
                },
                'required': ['so_number', 'customer_id', 'order_date', 'total_amount', 'status']
            },
            'description': 'Sales Order data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Sales Order created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'so_number': 'SO-001',
                    'customer_id': 123,
                    'status': 'Draft'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
@token_required
def create_sales_order():
    """Create a new sales order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_sales_order_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    item = {
        'id': str(uuid.uuid4()),
        'tenant_id': request.tenant_id,
        'so_number': data['so_number'],
        'customer_id': data['customer_id'],
        'customer_name': data.get('customer_name', ''),
        'customer_email': data.get('customer_email', ''),
        'customer_phone': data.get('customer_phone', ''),
        'order_date': data['order_date'],
        'delivery_date': data.get('delivery_date', None),
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data.get('subtotal', 0.0),
        'cgst_amount': data.get('cgst_amount', 0.0),
        'sgst_amount': data.get('sgst_amount', 0.0),
        'igst_amount': data.get('igst_amount', 0.0),
        'total_tax': data.get('total_tax', 0.0),
        'total_amount': data['total_amount'],
        'status': data['status'],
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'is_gst_applicable': data.get('is_gst_applicable', False),
        'subject': data.get('subject', ''),
        'salesperson': data.get('salesperson', ''),
        'items': data.get('items', []),
        'converted_to_invoice_id': data.get('converted_to_invoice_id', None),
        'converted_to_po_id': data.get('converted_to_po_id', None),
        'converted_from_quote_id': data.get('converted_from_quote_id', None),
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = sales_orders_container.create_item(body=item)
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create sales order: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders', methods=['GET'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by status'
        },
        {
            'name': 'customer_id',
            'in': 'query',
            'type': 'integer',
            'description': 'Filter by customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of sales orders',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
@token_required
def get_sales_orders():
    """Get all sales orders with optional filters"""
    try:
        status_filter = request.args.get('status')
        customer_id_filter = request.args.get('customer_id', type=int)

        _ALLOWED_SORT_FIELDS = {'created_at', 'so_number', 'order_date', 'delivery_date', 'total_amount'}
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'created_at'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        conditions = []
        parameters = [{"name": "@tenant_id", "value": request.tenant_id}]

        if status_filter:
            conditions.append("c.status = @status")
            parameters.append({"name": "@status", "value": status_filter})
        if customer_id_filter:
            conditions.append("c.customer_id = @customer_id")
            parameters.append({"name": "@customer_id", "value": customer_id_filter})

        if conditions:
            query += " AND " + " AND ".join(conditions)

        query += f" ORDER BY c.{sort_by} {sort_order}"

        items = list(sales_orders_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve sales orders: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/<so_id>', methods=['GET'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'so_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Sales Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sales Order retrieved successfully'
        },
        '404': {
            'description': 'Sales Order not found'
        }
    }
})
@token_required
def get_sales_order(so_id):
    """Get a sales order by ID"""
    try:
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": so_id},
                {"name": "@tenant_id", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Sales Order not found"}), 404
        
        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve sales order: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/<so_id>', methods=['PUT'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'so_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Sales Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'so_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'customer_name': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Confirmed', 'Closed', 'Invoiced', 'Cancelled']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'items': {'type': 'array'}
                }
            },
            'description': 'Updated sales order data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sales Order updated successfully'
        },
        '404': {
            'description': 'Sales Order not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
@token_required
def update_sales_order(so_id):
    """Update a sales order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_sales_order_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing sales order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": so_id},
                {"name": "@tenant_id", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Sales Order not found"}), 404
        
        so = items[0]
        
        # Update fields
        updatable_fields = [
            'so_number', 'customer_id', 'customer_name', 'customer_email', 'customer_phone',
            'order_date', 'delivery_date', 'payment_terms', 'subtotal', 'cgst_amount',
            'sgst_amount', 'igst_amount', 'total_tax', 'total_amount', 'status',
            'notes', 'terms_conditions', 'is_gst_applicable', 'subject', 'salesperson', 'items'
        ]
        
        for field in updatable_fields:
            if field in data:
                so[field] = data[field]
        
        so['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = sales_orders_container.replace_item(
            item=so['id'],
            body=so
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update sales order: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/<so_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'so_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Sales Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sales Order deleted successfully'
        },
        '404': {
            'description': 'Sales Order not found'
        }
    }
})
@token_required
def delete_sales_order(so_id):
    """Delete a sales order"""
    try:
        # Fetch the sales order to get partition key
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": so_id},
                {"name": "@tenant_id", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Sales Order not found"}), 404
        
        so = items[0]
        
        # Check if already converted to invoice
        if so.get('status') == 'Invoiced':
            return jsonify({"error": "Cannot delete a sales order that has been invoiced"}), 400
        
        sales_orders_container.delete_item(
            item=so['id'],
            partition_key=so['customer_id']
        )
        
        return jsonify({"message": "Sales Order deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete sales order: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/<so_id>/convert-invoice', methods=['POST'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'so_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Sales Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'invoice_number': {'type': 'string'}
                },
                'required': ['invoice_number']
            },
            'description': 'Invoice number for the new invoice'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sales Order converted to invoice successfully',
            'examples': {
                'application/json': {
                    'message': 'Sales Order converted to invoice successfully',
                    'invoice_id': 'uuid',
                    'invoice_number': 'INV-001'
                }
            }
        },
        '400': {
            'description': 'Sales Order already invoiced or validation error'
        },
        '404': {
            'description': 'Sales Order not found'
        }
    }
})
@token_required
def convert_so_to_invoice(so_id):
    """Convert a sales order to an invoice"""
    data = request.get_json()
    invoice_number = data.get('invoice_number')
    
    if not invoice_number:
        return jsonify({"error": "invoice_number is required"}), 400
    
    try:
        # Fetch the sales order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": so_id},
                {"name": "@tenant_id", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Sales Order not found"}), 404
        
        so = items[0]
        
        # Check if already invoiced
        if so.get('status') == 'Invoiced' or so.get('converted_to_invoice_id'):
            return jsonify({"error": "Sales Order has already been invoiced"}), 400
        
        # Create invoice from sales order
        now = datetime.utcnow().isoformat()
        invoice = {
            'id': str(uuid.uuid4()),
            'tenant_id': request.tenant_id,
            'invoice_number': invoice_number,
            'customer_id': so['customer_id'],
            'customer_name': so.get('customer_name', ''),
            'customer_email': so.get('customer_email', ''),
            'customer_phone': so.get('customer_phone', ''),
            'issue_date': datetime.utcnow().date().isoformat(),
            'due_date': so.get('delivery_date', datetime.utcnow().date().isoformat()),
            'payment_terms': so.get('payment_terms', ''),
            'subtotal': so.get('subtotal', 0.0),
            'cgst_amount': so.get('cgst_amount', 0.0),
            'sgst_amount': so.get('sgst_amount', 0.0),
            'igst_amount': so.get('igst_amount', 0.0),
            'total_tax': so.get('total_tax', 0.0),
            'total_amount': so['total_amount'],
            'amount_paid': 0.0,
            'balance_due': so['total_amount'],
            'status': 'Draft',
            'payment_mode': '',
            'notes': so.get('notes', ''),
            'terms_conditions': so.get('terms_conditions', ''),
            'is_gst_applicable': so.get('is_gst_applicable', False),
            'invoice_type': 'Tax Invoice',
            'subject': so.get('subject', ''),
            'salesperson': so.get('salesperson', ''),
            'items': so.get('items', []),
            'converted_from_so_id': so_id,
            'created_at': now,
            'updated_at': now
        }
        
        created_invoice = invoices_container.create_item(body=invoice)
        
        # Update sales order status
        so['status'] = 'Invoiced'
        so['converted_to_invoice_id'] = created_invoice['id']
        so['updated_at'] = now
        
        sales_orders_container.replace_item(
            item=so['id'],
            body=so
        )
        
        return jsonify({
            "message": "Sales Order converted to invoice successfully",
            "invoice_id": created_invoice['id'],
            "invoice_number": created_invoice['invoice_number']
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to convert sales order to invoice: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/<so_id>/convert-po', methods=['POST'])
@swag_from({
    'tags': ['Sales Orders'],
    'parameters': [
        {
            'name': 'so_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Sales Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'po_number': {'type': 'string'}
                },
                'required': ['po_number']
            },
            'description': 'Purchase Order number'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sales Order converted to PO successfully',
            'examples': {
                'application/json': {
                    'message': 'Sales Order converted to purchase order successfully',
                    'po_id': 'uuid',
                    'po_number': 'PO-001'
                }
            }
        },
        '400': {
            'description': 'Validation error or Sales Order already converted'
        },
        '404': {
            'description': 'Sales Order not found'
        }
    }
})
@token_required
def convert_so_to_po(so_id):
    """Convert a sales order to a purchase order"""
    data = request.get_json()
    po_number = data.get('po_number')
    
    if not po_number:
        return jsonify({"error": "po_number is required"}), 400
    
    try:
        # Fetch the sales order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": so_id},
                {"name": "@tenant_id", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Sales Order not found"}), 404
        
        so = items[0]
        
        # Check if already converted to PO
        if so.get('converted_to_po_id'):
            return jsonify({"error": "Sales Order has already been converted to a purchase order"}), 400
        
        # TODO: Create purchase order when PO module is implemented
        # For now, just update the sales order with a placeholder PO ID
        now = datetime.utcnow().isoformat()
        po_id = str(uuid.uuid4())
        
        so['converted_to_po_id'] = po_id
        so['updated_at'] = now
        
        sales_orders_container.replace_item(
            item=so['id'],
            body=so
        )
        
        return jsonify({
            "message": "Sales Order converted to purchase order successfully (PO module pending implementation)",
            "po_id": po_id,
            "po_number": po_number
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to convert sales order to PO: {str(e)}"}), 500

@sales_orders_blueprint.route('/sales-orders/next-number', methods=['GET'])
@swag_from({
    'tags': ['Sales Orders'],
    'responses': {
        '200': {
            'description': 'Next available SO number',
            'examples': {
                'application/json': {
                    'next_number': 'SO-001'
                }
            }
        }
    }
})
@token_required
def get_next_so_number():
    """Get the next available sales order number"""
    try:
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(sales_orders_container.query_items(
            query=query,
            parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "SO-001"}), 200
        
        last_so = items[0]
        last_number = last_so.get('so_number', 'SO-000')
        
        # Extract number part (assuming format SO-XXX)
        try:
            prefix, num_str = last_number.rsplit('-', 1)
            next_num = int(num_str) + 1
            next_number = f"{prefix}-{next_num:03d}"
        except:
            next_number = "SO-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate next SO number: {str(e)}"}), 500


@sales_orders_blueprint.route('/sales-orders/<so_id>/pdf', methods=['GET'])
@token_required
def get_so_pdf(so_id):
    """Generate and return a PDF for a sales order."""
    items = list(sales_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": so_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Sales order not found'}), 404
    so = items[0]
    doc = {
        **so,
        'invoice_number': so.get('so_number', so['id']),
        'items': [
            {**item, 'name': item.get('item_name', item.get('name', ''))}
            for item in so.get('items', [])
        ]
    }
    try:
        branding = _get_tenant_branding(request.tenant_id)
        pdf_bytes = build_invoice_pdf(doc, branding=branding)
        ref = so.get('so_number', 'so').replace('/', '-')
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={ref}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@sales_orders_blueprint.route('/sales-orders/<so_id>/send-email', methods=['POST'])
@token_required
def send_so_email(so_id):
    """Send a sales order to the customer via Azure Communication Services."""
    import os
    from azure.communication.email import EmailClient

    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address    = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')
    if not connection_string:
        return jsonify({'error': 'Email service not configured on the server'}), 503

    data = request.get_json() or {}
    attach_pdf = bool(data.get('attach_pdf', False))

    items = list(sales_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": so_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Sales order not found'}), 404
    so = items[0]

    recipient_email = data.get('recipient_email') or so.get('customer_email', '').strip()
    if not recipient_email:
        return jsonify({'error': 'No recipient email found on this sales order'}), 400

    so_number     = so.get('so_number', so['id'])
    customer_name = so.get('customer_name', 'Customer')
    order_date    = so.get('order_date', '')
    total_amount  = float(so.get('total_amount', 0))
    personal_msg  = data.get('message', '')

    _branding = _get_tenant_branding(request.tenant_id)
    _primary  = _branding.get('primary_color', '#2563EB')

    item_rows_html = ''
    for line in so.get('items', []):
        item_rows_html += (
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{line.get('item_name', line.get('name', ''))}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{float(line.get('quantity', 0)):.2f}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('rate', 0)):,.2f}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('amount', 0)):,.2f}</td>"
            f"</tr>"
        )

    personal_msg_html = f"<p style='color:#475569'>{personal_msg}</p>" if personal_msg else ''
    html_content = f"""
    <html><body style='font-family:Inter,Arial,sans-serif;color:#0F172A;max-width:640px;margin:auto'>
        <div style='background:{_primary};padding:24px;border-radius:8px 8px 0 0'>
            <h2 style='color:#fff;margin:0'>Sales Order {so_number}</h2>
        </div>
        <div style='background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px'>
            <p>Dear {customer_name},</p>
            {personal_msg_html}
            <p>Please find your sales order details below:</p>
            <table style='width:100%;border-collapse:collapse;margin:16px 0'>
                <thead><tr style='background:#F8FAFC'>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>Item</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Qty</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Rate</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Amount</th>
                </tr></thead>
                <tbody>{item_rows_html}</tbody>
            </table>
            <p style='font-size:18px;font-weight:bold'>Total: \u20b9{total_amount:,.2f}</p>
            <p style='color:#475569'><strong>Order Date:</strong> {order_date}</p>
            <p style='color:#94A3B8;font-size:12px;margin-top:32px'>This is an automated email from Solidev Books.</p>
        </div>
    </body></html>
    """

    email_message = {
        "senderAddress": sender_address,
        "recipients": {"to": [{"address": recipient_email}]},
        "content": {
            "subject": f"Sales Order {so_number}",
            "html": html_content
        }
    }

    if attach_pdf:
        try:
            doc = {
                **so,
                'invoice_number': so_number,
                'items': [{**i, 'name': i.get('item_name', i.get('name', ''))} for i in so.get('items', [])]
            }
            pdf_bytes = build_invoice_pdf(doc, branding=_branding)
            email_message["attachments"] = [{
                "name": f"so_{so_number}.pdf",
                "contentType": "application/pdf",
                "contentInBase64": base64.b64encode(pdf_bytes).decode('utf-8')
            }]
        except Exception as pdf_err:
            print(f"WARNING: SO PDF generation failed: {pdf_err}")

    try:
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send(email_message)
        result = poller.result()
        return jsonify({
            'message':    'Sales order email sent successfully',
            'sent_to':    recipient_email,
            'message_id': result.get('id'),
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500

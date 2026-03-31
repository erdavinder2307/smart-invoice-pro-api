from flask import Blueprint, request, jsonify, make_response
from smart_invoice_pro.utils.cosmos_client import purchase_orders_container, bills_container
import uuid
import base64
from flasgger import swag_from
from datetime import datetime
from enum import Enum
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding

purchase_orders_blueprint = Blueprint('purchase_orders', __name__)

class POStatus(Enum):
    Draft = 'Draft'
    Sent = 'Sent'
    Confirmed = 'Confirmed'
    Received = 'Received'
    Billed = 'Billed'
    Closed = 'Closed'
    Cancelled = 'Cancelled'

def validate_po_data(data, is_update=False):
    """Validate purchase order data"""
    errors = {}
    
    if not is_update:
        required_fields = ['po_number', 'vendor_id', 'order_date', 'total_amount', 'status']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate status
    if 'status' in data and data['status'] not in POStatus._value2member_map_:
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

@purchase_orders_blueprint.route('/purchase-orders', methods=['POST'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'po_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Sent', 'Confirmed', 'Received', 'Billed', 'Closed', 'Cancelled']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'items': {'type': 'array'},
                    'converted_to_bill_id': {'type': 'string'}
                },
                'required': ['po_number', 'vendor_id', 'order_date', 'total_amount', 'status']
            },
            'description': 'Purchase Order data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Purchase Order created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'po_number': 'PO-001',
                    'vendor_id': '123',
                    'status': 'Draft'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_purchase_order():
    """Create a new purchase order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_po_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    item = {
        'id': str(uuid.uuid4()),
        'po_number': data['po_number'],
        'vendor_id': data['vendor_id'],
        'vendor_name': data.get('vendor_name', ''),
        'order_date': data['order_date'],
        'delivery_date': data.get('delivery_date', None),
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data.get('subtotal', 0.0),
        'tax_amount': data.get('tax_amount', 0.0),
        'total_amount': data['total_amount'],
        'status': data['status'],
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'items': data.get('items', []),
        'converted_to_bill_id': data.get('converted_to_bill_id', None),
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = purchase_orders_container.create_item(body=item)
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders', methods=['GET'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by status'
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
            'description': 'List of purchase orders',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_purchase_orders():
    """Get all purchase orders with optional filters"""
    try:
        status_filter = request.args.get('status')
        vendor_id_filter = request.args.get('vendor_id')
        
        query = "SELECT * FROM c"
        conditions = []
        
        if status_filter:
            conditions.append(f"c.status = '{status_filter}'")
        if vendor_id_filter:
            conditions.append(f"c.vendor_id = '{vendor_id_filter}'")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY c.created_at DESC"
        
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve purchase orders: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['GET'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order retrieved successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def get_purchase_order(po_id):
    """Get a purchase order by ID"""
    try:
        query = f"SELECT * FROM c WHERE c.id = '{po_id}'"
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['PUT'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'po_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'items': {'type': 'array'}
                }
            },
            'description': 'Updated purchase order data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order updated successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def update_purchase_order(po_id):
    """Update a purchase order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_po_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing purchase order
        query = f"SELECT * FROM c WHERE c.id = '{po_id}'"
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        po = items[0]
        
        # Update fields
        updatable_fields = [
            'po_number', 'vendor_id', 'vendor_name', 'order_date', 'delivery_date',
            'payment_terms', 'subtotal', 'tax_amount', 'total_amount', 'status',
            'notes', 'terms_conditions', 'items'
        ]
        
        for field in updatable_fields:
            if field in data:
                po[field] = data[field]
        
        po['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = purchase_orders_container.replace_item(
            item=po['id'],
            body=po
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order deleted successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def delete_purchase_order(po_id):
    """Delete a purchase order"""
    try:
        # Fetch the purchase order to get partition key
        query = f"SELECT * FROM c WHERE c.id = '{po_id}'"
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        po = items[0]
        
        # Check if already converted to bill
        if po.get('status') == 'Billed':
            return jsonify({"error": "Cannot delete a purchase order that has been billed"}), 400
        
        purchase_orders_container.delete_item(
            item=po['id'],
            partition_key=po['vendor_id']
        )
        
        return jsonify({"message": "Purchase Order deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>/convert-bill', methods=['POST'])
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'}
                },
                'required': ['bill_number']
            },
            'description': 'Bill number for the new bill'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order converted to bill successfully',
            'examples': {
                'application/json': {
                    'message': 'Purchase Order converted to bill successfully',
                    'bill_id': 'uuid',
                    'bill_number': 'BILL-001'
                }
            }
        },
        '400': {
            'description': 'Purchase Order already billed or validation error'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def convert_po_to_bill(po_id):
    """Convert a purchase order to a bill"""
    data = request.get_json()
    bill_number = data.get('bill_number')
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    try:
        # Fetch the purchase order
        query = f"SELECT * FROM c WHERE c.id = '{po_id}'"
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        po = items[0]
        
        # Check if already billed
        if po.get('status') == 'Billed' or po.get('converted_to_bill_id'):
            return jsonify({"error": "Purchase Order has already been billed"}), 400
        
        # Create bill from purchase order
        now = datetime.utcnow().isoformat()
        bill = {
            'id': str(uuid.uuid4()),
            'bill_number': bill_number,
            'vendor_id': po['vendor_id'],
            'vendor_name': po.get('vendor_name', ''),
            'bill_date': datetime.utcnow().date().isoformat(),
            'due_date': po.get('delivery_date', datetime.utcnow().date().isoformat()),
            'payment_terms': po.get('payment_terms', ''),
            'subtotal': po.get('subtotal', 0.0),
            'tax_amount': po.get('tax_amount', 0.0),
            'total_amount': po['total_amount'],
            'amount_paid': 0.0,
            'balance_due': po['total_amount'],
            'payment_status': 'Unpaid',
            'notes': po.get('notes', ''),
            'terms_conditions': po.get('terms_conditions', ''),
            'items': po.get('items', []),
            'expenses': [],
            'converted_from_po_id': po_id,
            'created_at': now,
            'updated_at': now
        }
        
        created_bill = bills_container.create_item(body=bill)
        
        # Update purchase order status
        po['status'] = 'Billed'
        po['converted_to_bill_id'] = created_bill['id']
        po['updated_at'] = now
        
        purchase_orders_container.replace_item(
            item=po['id'],
            body=po
        )
        
        return jsonify({
            "message": "Purchase Order converted to bill successfully",
            "bill_id": created_bill['id'],
            "bill_number": created_bill['bill_number']
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to convert purchase order to bill: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/next-number', methods=['GET'])
@swag_from({
    'tags': ['Purchase Orders'],
    'responses': {
        '200': {
            'description': 'Next available PO number',
            'examples': {
                'application/json': {
                    'next_number': 'PO-001'
                }
            }
        }
    }
})
def get_next_po_number():
    """Get the next available purchase order number"""
    try:
        query = "SELECT * FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(purchase_orders_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "PO-001"}), 200
        
        last_po = items[0]
        last_number = last_po.get('po_number', 'PO-000')
        
        # Extract number part (assuming format PO-XXX)
        try:
            prefix, num_str = last_number.rsplit('-', 1)
            next_num = int(num_str) + 1
            next_number = f"{prefix}-{next_num:03d}"
        except:
            next_number = "PO-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate next PO number: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>/pdf', methods=['GET'])
def get_po_pdf(po_id):
    """Generate and return a PDF for a purchase order."""
    items = list(purchase_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": po_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Purchase order not found'}), 404
    po = items[0]
    doc = {
        **po,
        'invoice_number': po.get('po_number', po['id']),
        'customer_name': po.get('vendor_name', po.get('vendor_id', 'Vendor')),
        'items': [
            {**item, 'name': item.get('item_name', item.get('name', ''))}
            for item in po.get('items', [])
        ]
    }
    try:
        branding = _get_tenant_branding(request.tenant_id)
        pdf_bytes = build_invoice_pdf(doc, branding=branding)
        ref = po.get('po_number', 'po').replace('/', '-')
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={ref}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@purchase_orders_blueprint.route('/purchase-orders/<po_id>/send-email', methods=['POST'])
def send_po_email(po_id):
    """Send a purchase order to the vendor via Azure Communication Services."""
    import os
    from azure.communication.email import EmailClient

    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address    = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')
    if not connection_string:
        return jsonify({'error': 'Email service not configured on the server'}), 503

    data = request.get_json() or {}
    attach_pdf = bool(data.get('attach_pdf', False))

    items = list(purchase_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": po_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Purchase order not found'}), 404
    po = items[0]

    recipient_email = data.get('recipient_email') or po.get('vendor_email', '').strip()
    if not recipient_email:
        return jsonify({'error': 'No recipient email found on this purchase order'}), 400

    po_number     = po.get('po_number', po['id'])
    vendor_name   = po.get('vendor_name', 'Vendor')
    order_date    = po.get('order_date', '')
    delivery_date = po.get('delivery_date', po.get('expected_delivery', ''))
    total_amount  = float(po.get('total_amount', 0))
    personal_msg  = data.get('message', '')

    _branding = _get_tenant_branding(request.tenant_id)
    _primary  = _branding.get('primary_color', '#2563EB')

    item_rows_html = ''
    for line in po.get('items', []):
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
            <h2 style='color:#fff;margin:0'>Purchase Order {po_number}</h2>
        </div>
        <div style='background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px'>
            <p>Dear {vendor_name},</p>
            {personal_msg_html}
            <p>Please find the purchase order details below:</p>
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
            <p style='color:#94A3B8;font-size:12px;margin-top:32px'>This is an automated email from Smart Invoice Pro.</p>
        </div>
    </body></html>
    """

    email_message = {
        "senderAddress": sender_address,
        "recipients": {"to": [{"address": recipient_email}]},
        "content": {
            "subject": f"Purchase Order {po_number}",
            "html": html_content
        }
    }

    if attach_pdf:
        try:
            doc = {
                **po,
                'invoice_number': po_number,
                'customer_name': vendor_name,
                'items': [{**i, 'name': i.get('item_name', i.get('name', ''))} for i in po.get('items', [])]
            }
            pdf_bytes = build_invoice_pdf(doc, branding=_branding)
            email_message["attachments"] = [{
                "name": f"po_{po_number}.pdf",
                "contentType": "application/pdf",
                "contentInBase64": base64.b64encode(pdf_bytes).decode('utf-8')
            }]
        except Exception as pdf_err:
            print(f"WARNING: PO PDF generation failed: {pdf_err}")

    try:
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send(email_message)
        result = poller.result()
        return jsonify({
            'message':    'Purchase order email sent successfully',
            'sent_to':    recipient_email,
            'message_id': result.get('id'),
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500
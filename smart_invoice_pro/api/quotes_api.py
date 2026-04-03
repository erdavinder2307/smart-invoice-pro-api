from flask import Blueprint, request, jsonify, make_response
from smart_invoice_pro.utils.cosmos_client import quotes_container, invoices_container, sales_orders_container
import uuid
import base64
from flasgger import swag_from
from datetime import datetime
from enum import Enum
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding

quotes_blueprint = Blueprint('quotes', __name__)

class QuoteStatus(Enum):
    Draft = 'Draft'
    Sent = 'Sent'
    Accepted = 'Accepted'
    Declined = 'Declined'
    Expired = 'Expired'
    Converted = 'Converted'

def validate_quote_data(data, is_update=False):
    """Validate quote data"""
    errors = {}
    
    if not is_update:
        required_fields = ['quote_number', 'customer_id', 'issue_date', 'expiry_date', 'total_amount', 'status']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate status
    if 'status' in data and data['status'] not in QuoteStatus._value2member_map_:
        errors['status'] = f'Invalid status: {data["status"]}'
    
    # Validate dates
    if 'issue_date' in data and 'expiry_date' in data:
        try:
            issue = datetime.fromisoformat(data['issue_date'].replace('Z', '+00:00'))
            expiry = datetime.fromisoformat(data['expiry_date'].replace('Z', '+00:00'))
            if expiry <= issue:
                errors['expiry_date'] = 'Expiry date must be after issue date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    return errors

@quotes_blueprint.route('/quotes', methods=['POST'])
@swag_from({
    'tags': ['Quotes'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'quote_number': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'customer_name': {'type': 'string'},
                    'customer_email': {'type': 'string'},
                    'customer_phone': {'type': 'string'},
                    'issue_date': {'type': 'string', 'format': 'date'},
                    'expiry_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'},
                    'total_tax': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Sent', 'Accepted', 'Declined', 'Expired', 'Converted']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'subject': {'type': 'string'},
                    'salesperson': {'type': 'string'},
                    'items': {'type': 'array'},
                    'converted_to_invoice_id': {'type': 'string'},
                    'converted_to_sales_order_id': {'type': 'string'}
                },
                'required': ['quote_number', 'customer_id', 'issue_date', 'expiry_date', 'total_amount', 'status']
            },
            'description': 'Quote data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Quote created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'quote_number': 'QT-001',
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
def create_quote():
    """Create a new quote"""
    data = request.get_json()
    
    # Validate data
    errors = validate_quote_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    item = {
        'id': str(uuid.uuid4()),
        'quote_number': data['quote_number'],
        'customer_id': data['customer_id'],
        'customer_name': data.get('customer_name', ''),
        'customer_email': data.get('customer_email', ''),
        'customer_phone': data.get('customer_phone', ''),
        'issue_date': data['issue_date'],
        'expiry_date': data['expiry_date'],
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
        'converted_to_sales_order_id': data.get('converted_to_sales_order_id', None),
        'tenant_id': request.tenant_id,
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = quotes_container.create_item(body=item)
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create quote: {str(e)}"}), 500

@quotes_blueprint.route('/quotes', methods=['GET'])
@swag_from({
    'tags': ['Quotes'],
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
            'description': 'List of quotes',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_quotes():
    try:
        status_filter = request.args.get('status')
        customer_id_filter = request.args.get('customer_id')
        
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        parameters = [{"name": "@tenant_id", "value": request.tenant_id}]
        
        if status_filter:
            query += " AND c.status = @status"
            parameters.append({"name": "@status", "value": status_filter})
        if customer_id_filter:
            query += " AND c.customer_id = @customer_id"
            parameters.append({"name": "@customer_id", "value": customer_id_filter})
        
        query += " ORDER BY c.created_at DESC"
        
        items = list(quotes_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch quotes: {str(e)}"}), 500

@quotes_blueprint.route('/quotes/<quote_id>', methods=['GET'])
@swag_from({
    'tags': ['Quotes'],
    'parameters': [
        {
            'name': 'quote_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Quote ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Quote details'
        },
        '404': {
            'description': 'Quote not found'
        }
    }
})
def get_quote(quote_id):
    """Get a specific quote by ID"""
    try:
        query = "SELECT * FROM c WHERE c.id = @id"
        items = list(quotes_container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": quote_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Quote not found"}), 404
            
        quote = items[0]
        if quote.get('tenant_id') != request.tenant_id:
            return jsonify({"error": "Forbidden"}), 403
        
        return jsonify(quote), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch quote: {str(e)}"}), 500

@quotes_blueprint.route('/quotes/<quote_id>', methods=['PUT'])
@swag_from({
    'tags': ['Quotes'],
    'parameters': [
        {
            'name': 'quote_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Quote ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'items': {'type': 'array'}
                }
            },
            'description': 'Fields to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Quote updated successfully'
        },
        '404': {
            'description': 'Quote not found'
        }
    }
})
def update_quote(quote_id):
    """Update an existing quote"""
    data = request.get_json()
    
    # Validate data
    errors = validate_quote_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing quote
        query = "SELECT * FROM c WHERE c.id = @id"
        items = list(quotes_container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": quote_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Quote not found"}), 404
            
        existing_quote = items[0]
        if existing_quote.get('tenant_id') != request.tenant_id:
            return jsonify({"error": "Forbidden"}), 403
        
        # Update fields
        for key, value in data.items():
            if key != 'id' and key != 'created_at':
                existing_quote[key] = value
        
        existing_quote['updated_at'] = datetime.utcnow().isoformat()
        
        # Replace the item
        updated_item = quotes_container.replace_item(
            item=existing_quote['id'],
            body=existing_quote
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update quote: {str(e)}"}), 500

@quotes_blueprint.route('/quotes/<quote_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Quotes'],
    'parameters': [
        {
            'name': 'quote_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Quote ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Quote deleted successfully'
        },
        '404': {
            'description': 'Quote not found'
        }
    }
})
def delete_quote(quote_id):
    """Delete a quote"""
    try:
        # Fetch existing quote to get partition key
        query = "SELECT * FROM c WHERE c.id = @id"
        items = list(quotes_container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": quote_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Quote not found"}), 404
        
        quote = items[0]
        if quote.get('tenant_id') != request.tenant_id:
            return jsonify({"error": "Forbidden"}), 403
        
        # Delete the quote
        quotes_container.delete_item(
            item=quote_id,
            partition_key=quote['customer_id']
        )
        
        return jsonify({"message": "Quote deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete quote: {str(e)}"}), 500

@quotes_blueprint.route('/quotes/<quote_id>/convert', methods=['POST'])
@swag_from({
    'tags': ['Quotes'],
    'parameters': [
        {
            'name': 'quote_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Quote ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'convert_to': {'type': 'string', 'enum': ['invoice', 'sales_order']},
                    'invoice_number': {'type': 'string', 'description': 'Required if converting to invoice'}
                },
                'required': ['convert_to']
            },
            'description': 'Conversion type'
        }
    ],
    'responses': {
        '200': {
            'description': 'Quote converted successfully',
            'examples': {
                'application/json': {
                    'message': 'Quote converted to invoice successfully',
                    'invoice_id': 'uuid',
                    'invoice_number': 'INV-001'
                }
            }
        },
        '400': {
            'description': 'Invalid conversion type or quote already converted'
        },
        '404': {
            'description': 'Quote not found'
        }
    }
})
def convert_quote(quote_id):
    """Convert a quote to an invoice or sales order"""
    data = request.get_json()
    convert_to = data.get('convert_to')
    
    if convert_to not in ['invoice', 'sales_order']:
        return jsonify({"error": "Invalid conversion type. Must be 'invoice' or 'sales_order'"}), 400
    
    try:
        # Fetch the quote
        query = "SELECT * FROM c WHERE c.id = @id"
        items = list(quotes_container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": quote_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Quote not found"}), 404
            
        quote = items[0]
        if quote.get('tenant_id') != request.tenant_id:
            return jsonify({"error": "Forbidden"}), 403
        
        # Check if already converted
        if quote.get('status') == 'Converted':
            return jsonify({"error": "Quote has already been converted"}), 400
        
        # Check if expired
        try:
            expiry_date = datetime.fromisoformat(quote['expiry_date'].replace('Z', '+00:00'))
            if expiry_date < datetime.now(expiry_date.tzinfo):
                return jsonify({"error": "Quote has expired"}), 400
        except:
            pass
        
        if convert_to == 'invoice':
            # Create invoice from quote
            invoice_number = data.get('invoice_number')
            if not invoice_number:
                return jsonify({"error": "invoice_number is required for invoice conversion"}), 400
            
            now = datetime.utcnow().isoformat()
            invoice = {
                'id': str(uuid.uuid4()),
                'invoice_number': invoice_number,
                'customer_id': quote['customer_id'],
                'customer_name': quote.get('customer_name', ''),
                'customer_email': quote.get('customer_email', ''),
                'customer_phone': quote.get('customer_phone', ''),
                'issue_date': datetime.utcnow().date().isoformat(),
                'due_date': quote.get('expiry_date', datetime.utcnow().date().isoformat()),
                'payment_terms': quote.get('payment_terms', ''),
                'subtotal': quote.get('subtotal', 0.0),
                'cgst_amount': quote.get('cgst_amount', 0.0),
                'sgst_amount': quote.get('sgst_amount', 0.0),
                'igst_amount': quote.get('igst_amount', 0.0),
                'total_tax': quote.get('total_tax', 0.0),
                'total_amount': quote['total_amount'],
                'amount_paid': 0.0,
                'balance_due': quote['total_amount'],
                'status': 'Draft',
                'payment_mode': '',
                'notes': quote.get('notes', ''),
                'terms_conditions': quote.get('terms_conditions', ''),
                'is_gst_applicable': quote.get('is_gst_applicable', False),
                'invoice_type': 'Tax Invoice',
                'subject': quote.get('subject', ''),
                'salesperson': quote.get('salesperson', ''),
                'items': quote.get('items', []),
                'converted_from_quote_id': quote_id,
                'tenant_id': request.tenant_id,
                'created_at': now,
                'updated_at': now
            }
            
            created_invoice = invoices_container.create_item(body=invoice)
            
            # Update quote status
            quote['status'] = 'Converted'
            quote['converted_to_invoice_id'] = created_invoice['id']
            quote['updated_at'] = now
            
            quotes_container.replace_item(
                item=quote['id'],
                body=quote
            )
            
            return jsonify({
                "message": "Quote converted to invoice successfully",
                "invoice_id": created_invoice['id'],
                "invoice_number": created_invoice['invoice_number']
            }), 200
        
        elif convert_to == 'sales_order':
            # Create sales order from quote
            so_number = data.get('so_number')
            if not so_number:
                return jsonify({"error": "so_number is required for sales order conversion"}), 400
            
            now = datetime.utcnow().isoformat()
            sales_order = {
                'id': str(uuid.uuid4()),
                'so_number': so_number,
                'customer_id': quote['customer_id'],
                'customer_name': quote.get('customer_name', ''),
                'customer_email': quote.get('customer_email', ''),
                'customer_phone': quote.get('customer_phone', ''),
                'order_date': datetime.utcnow().date().isoformat(),
                'delivery_date': quote.get('expiry_date', None),
                'payment_terms': quote.get('payment_terms', ''),
                'subtotal': quote.get('subtotal', 0.0),
                'cgst_amount': quote.get('cgst_amount', 0.0),
                'sgst_amount': quote.get('sgst_amount', 0.0),
                'igst_amount': quote.get('igst_amount', 0.0),
                'total_tax': quote.get('total_tax', 0.0),
                'total_amount': quote['total_amount'],
                'status': 'Draft',
                'notes': quote.get('notes', ''),
                'terms_conditions': quote.get('terms_conditions', ''),
                'is_gst_applicable': quote.get('is_gst_applicable', False),
                'subject': quote.get('subject', ''),
                'salesperson': quote.get('salesperson', ''),
                'items': quote.get('items', []),
                'converted_from_quote_id': quote_id,
                'tenant_id': request.tenant_id,
                'created_at': now,
                'updated_at': now
            }
            
            created_so = sales_orders_container.create_item(body=sales_order)
            
            # Update quote status
            quote['status'] = 'Converted'
            quote['converted_to_sales_order_id'] = created_so['id']
            quote['updated_at'] = now
            
            quotes_container.replace_item(
                item=quote['id'],
                body=quote
            )
            
            return jsonify({
                "message": "Quote converted to sales order successfully",
                "sales_order_id": created_so['id'],
                "so_number": created_so['so_number']
            }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to convert quote: {str(e)}"}), 500

@quotes_blueprint.route('/quotes/next-number', methods=['GET'])
@swag_from({
    'tags': ['Quotes'],
    'responses': {
        '200': {
            'description': 'Next quote number',
            'examples': {
                'application/json': {
                    'next_number': 'QT-001'
                }
            }
        }
    }
})
def get_next_quote_number():
    """Generate the next quote number"""
    try:
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(quotes_container.query_items(
            query=query,
            parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "QT-001"}), 200
        
        last_quote = items[0]
        last_number = last_quote.get('quote_number', 'QT-000')
        
        try:
            # Extract number part and increment
            prefix = 'QT-'
            if last_number.startswith(prefix):
                number = int(last_number.replace(prefix, ''))
                next_number = f"{prefix}{str(number + 1).zfill(3)}"
            else:
                next_number = "QT-001"
        except:
            next_number = "QT-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"next_number": "QT-001"}), 200


@quotes_blueprint.route('/quotes/<quote_id>/pdf', methods=['GET'])
def get_quote_pdf(quote_id):
    """Generate and return a PDF for a quote."""
    items = list(quotes_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": quote_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Quote not found'}), 404
    quote = items[0]
    doc = {**quote, 'invoice_number': quote.get('quote_number', quote['id'])}
    try:
        branding = _get_tenant_branding(request.tenant_id)
        pdf_bytes = build_invoice_pdf(doc, branding=branding)
        ref = quote.get('quote_number', 'quote').replace('/', '-')
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={ref}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@quotes_blueprint.route('/quotes/<quote_id>/send-email', methods=['POST'])
def send_quote_email(quote_id):
    """Send a quote to the customer via Azure Communication Services."""
    import os
    from azure.communication.email import EmailClient

    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address    = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')
    if not connection_string:
        return jsonify({'error': 'Email service not configured on the server'}), 503

    data = request.get_json() or {}
    attach_pdf = bool(data.get('attach_pdf', False))

    items = list(quotes_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": quote_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Quote not found'}), 404
    quote = items[0]

    recipient_email = data.get('recipient_email') or quote.get('customer_email', '').strip()
    if not recipient_email:
        return jsonify({'error': 'No recipient email found on this quote'}), 400

    quote_number  = quote.get('quote_number', quote['id'])
    customer_name = quote.get('customer_name', 'Customer')
    issue_date    = quote.get('issue_date', '')
    expiry_date   = quote.get('expiry_date', '')
    total_amount  = float(quote.get('total_amount', 0))
    personal_msg  = data.get('message', '')

    _branding = _get_tenant_branding(request.tenant_id)
    _primary  = _branding.get('primary_color', '#2563EB')

    item_rows_html = ''
    for line in quote.get('items', []):
        item_rows_html += (
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{line.get('name', line.get('item_name', ''))}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{float(line.get('quantity', 0)):.2f}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('rate', 0)):,.2f}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('amount', 0)):,.2f}</td>"
            f"</tr>"
        )

    personal_msg_html = f"<p style='color:#475569'>{personal_msg}</p>" if personal_msg else ''
    html_content = f"""
    <html><body style='font-family:Inter,Arial,sans-serif;color:#0F172A;max-width:640px;margin:auto'>
        <div style='background:{_primary};padding:24px;border-radius:8px 8px 0 0'>
            <h2 style='color:#fff;margin:0'>Quote {quote_number}</h2>
        </div>
        <div style='background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px'>
            <p>Dear {customer_name},</p>
            {personal_msg_html}
            <p>Please find your quotation details below:</p>
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
            <p style='color:#475569'><strong>Quote Date:</strong> {issue_date}&nbsp;|&nbsp;<strong>Valid Until:</strong> {expiry_date}</p>
            <p style='color:#94A3B8;font-size:12px;margin-top:32px'>This is an automated email from Solidev Books.</p>
        </div>
    </body></html>
    """

    email_message = {
        "senderAddress": sender_address,
        "recipients": {"to": [{"address": recipient_email}]},
        "content": {
            "subject": f"Quotation {quote_number} from us \u2014 Valid until {expiry_date}",
            "html": html_content
        }
    }

    if attach_pdf:
        try:
            doc = {**quote, 'invoice_number': quote_number}
            pdf_bytes = build_invoice_pdf(doc, branding=_branding)
            email_message["attachments"] = [{
                "name": f"quote_{quote_number}.pdf",
                "contentType": "application/pdf",
                "contentInBase64": base64.b64encode(pdf_bytes).decode('utf-8')
            }]
        except Exception as pdf_err:
            print(f"WARNING: Quote PDF generation failed: {pdf_err}")

    try:
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send(email_message)
        result = poller.result()

        quote['email_status']  = 'sent'
        quote['email_sent_at'] = datetime.utcnow().isoformat()
        quote['updated_at']    = datetime.utcnow().isoformat()
        if quote.get('status') == 'Draft':
            quote['status'] = 'Sent'
        quotes_container.replace_item(item=quote['id'], body=quote)

        return jsonify({
            'message':    'Quote email sent successfully',
            'sent_to':    recipient_email,
            'message_id': result.get('id'),
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500

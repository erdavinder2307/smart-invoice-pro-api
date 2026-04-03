from flask import Blueprint, request, jsonify, make_response
from smart_invoice_pro.utils.cosmos_client import invoices_container, get_container
from smart_invoice_pro.utils.response_sanitizer import sanitize_item, sanitize_items
from smart_invoice_pro.utils.webhook_dispatcher import dispatch_webhook_event
from smart_invoice_pro.utils.notifications import create_notification
from smart_invoice_pro.utils.audit_logger import log_audit
import copy
import uuid
import secrets
import base64
from flasgger import swag_from
from datetime import datetime, timedelta
from enum import Enum
import jwt
from functools import wraps
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding
from smart_invoice_pro.api.invoice_preferences_api import (
    generate_invoice_number,
    peek_next_invoice_number,
    _get_prefs,
    DEFAULT_PREFS,
)
from smart_invoice_pro.api.tax_rates_api import (
    calculate_gst,
    _get_seller_state,
    _get_customer_state,
)

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

    # ── Invoice preferences: auto-generate number, apply defaults ────────────
    prefs = _get_prefs(request.tenant_id)
    auto_gen = bool(prefs.get('auto_generate_invoice_number', DEFAULT_PREFS['auto_generate_invoice_number']))

    if auto_gen:
        invoice_number = generate_invoice_number(request.tenant_id)
    else:
        invoice_number = data.get('invoice_number') or data.get('invoice_number', '')
        if not invoice_number:
            return jsonify({'error': 'invoice_number is required when auto-generate is disabled.'}), 400

    # Apply default notes/terms/payment_terms from preferences if not supplied
    default_payment_terms = prefs.get('default_payment_terms', DEFAULT_PREFS['default_payment_terms'])
    default_notes         = prefs.get('default_notes',         DEFAULT_PREFS['default_notes'])
    default_terms         = prefs.get('default_terms',         DEFAULT_PREFS['default_terms'])
    default_due_days      = int(prefs.get('default_due_days',  DEFAULT_PREFS['default_due_days']))

    payment_terms = data.get('payment_terms') or default_payment_terms
    notes         = data.get('notes')         if data.get('notes')         is not None else default_notes
    terms_conds   = data.get('terms_conditions') if data.get('terms_conditions') is not None else default_terms

    # Auto-calculate due_date from issue_date + default_due_days if not provided
    issue_date = data['issue_date']
    due_date   = data.get('due_date') or ''
    if not due_date and issue_date:
        try:
            due_date = (datetime.strptime(issue_date, '%Y-%m-%d') + timedelta(days=default_due_days)).strftime('%Y-%m-%d')
        except ValueError:
            due_date = issue_date
    if not due_date:
        due_date = issue_date

    # ── Server-side GST calculation ──────────────────────────────────────────
    is_gst_applicable = bool(data.get('is_gst_applicable', False))
    raw_items = data.get('items', [])
    place_of_supply = (data.get('place_of_supply') or '').strip()

    if is_gst_applicable:
        try:
            seller_state = _get_seller_state(request.tenant_id)
            customer_id_str = str(data.get('customer_id', ''))
            customer_state, gst_treatment, customer_pos = _get_customer_state(
                request.tenant_id, customer_id_str
            )
            effective_pos = place_of_supply or customer_pos or customer_state
            gst_result = calculate_gst(
                items=raw_items,
                seller_state=seller_state,
                customer_state=customer_state,
                gst_treatment=gst_treatment,
                is_gst_applicable=True,
                place_of_supply=effective_pos,
            )
            cgst_amount  = gst_result['cgst_amount']
            sgst_amount  = gst_result['sgst_amount']
            igst_amount  = gst_result['igst_amount']
            total_tax    = gst_result['total_tax']
            stored_items = gst_result['items_with_tax']
            place_of_supply = effective_pos
        except Exception:
            # Fallback to client-supplied values on error
            cgst_amount  = data.get('cgst_amount', 0.0)
            sgst_amount  = data.get('sgst_amount', 0.0)
            igst_amount  = data.get('igst_amount', 0.0)
            total_tax    = data.get('total_tax', 0.0)
            stored_items = raw_items
            gst_treatment = data.get('gst_treatment', 'regular')
    else:
        cgst_amount  = 0.0
        sgst_amount  = 0.0
        igst_amount  = 0.0
        total_tax    = 0.0
        stored_items = raw_items
        gst_treatment = data.get('gst_treatment', 'regular')

    item = {
        'id': str(uuid.uuid4()),
        'invoice_number': invoice_number,
        'customer_id': data['customer_id'],
        'customer_name': data.get('customer_name', ''),
        'customer_email': data.get('customer_email', ''),
        'customer_phone': data.get('customer_phone', ''),
        'issue_date': issue_date,
        'due_date': due_date,
        'payment_terms': payment_terms,
        'subtotal': data['subtotal'],
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'igst_amount': igst_amount,
        'total_tax': total_tax,
        'total_amount': data['total_amount'],
        'amount_paid': data.get('amount_paid', 0.0),
        'balance_due': data.get('balance_due', data['total_amount']),
        'status': data['status'],
        'payment_mode': data.get('payment_mode', ''),
        'notes': notes,
        'terms_conditions': terms_conds,
        'is_gst_applicable': is_gst_applicable,
        'place_of_supply': place_of_supply,
        'gst_treatment': gst_treatment,
        'invoice_type': data.get('invoice_type', ''),
        'items': stored_items,
        'tenant_id': request.tenant_id,
        'portal_token': secrets.token_urlsafe(32),
        'created_at': data.get('created_at', now),
        'updated_at': data.get('updated_at', now)
    }
    invoices_container.create_item(body=item)
    
    # Decrement stock for each item in the invoice
    stock_container = get_container("stock", "/product_id")
    for invoice_item in stored_items:
        if 'product_id' in invoice_item and 'quantity' in invoice_item:
            try:
                stock_transaction = {
                    'id': str(uuid.uuid4()),
                    'product_id': str(invoice_item['product_id']),
                    'tenant_id': request.tenant_id,
                    'quantity': float(invoice_item['quantity']),
                    'type': 'OUT',
                    'source': f'Invoice {data["invoice_number"]}',
                    'reference_id': item['id'],
                    'timestamp': now
                }
                stock_container.create_item(body=stock_transaction)
            except Exception as e:
                print(f"Error updating stock for product {invoice_item.get('product_id')}: {str(e)}")
    
    dispatch_webhook_event(
        tenant_id=request.tenant_id,
        event="invoice.created",
        payload={"invoice_id": item["id"], "invoice_number": item.get("invoice_number"),
                 "total": item.get("total"), "status": item.get("status")},
    )
    create_notification(
        tenant_id=request.tenant_id,
        notification_type="invoice_created",
        title="Invoice Created",
        message=f"Invoice {item.get('invoice_number', item['id'])} for ₹{item.get('total_amount', 0):,.2f} has been created.",
        entity_id=item["id"],
        entity_type="invoice",
        user_id=getattr(request, 'user_id', None),
    )
    log_audit("invoice", "create", item["id"], None, item,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
    return jsonify(sanitize_item(item)), 201

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
    items = list(invoices_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tenant_id",
        parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
        enable_cross_partition_query=True
    ))
    return jsonify(sanitize_items(items))

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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(invoices_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    if items[0].get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify(sanitize_item(items[0]))

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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(invoices_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    before_snapshot = copy.deepcopy(item)
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
    log_audit("invoice", "update", invoice_id, before_snapshot, item,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
    return jsonify(sanitize_item(item))

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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(invoices_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    log_audit("invoice", "delete", invoice_id, item, None,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(invoices_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    before_snapshot = copy.deepcopy(item)
    for k, v in data.items():
        item[k] = v
    item['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.replace_item(item=item['id'], body=item)
    log_audit("invoice", "update", invoice_id, before_snapshot, item,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
    return jsonify({'message': 'Invoice updated', 'invoice': sanitize_item(item)})

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
    """Preview the next invoice number without incrementing (uses invoice preferences)."""
    try:
        next_invoice_number = peek_next_invoice_number(request.tenant_id)
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


# ── Public portal endpoint (no auth required) ────────────────────────────────
@api_blueprint.route('/portal/invoice/<token>', methods=['GET'])
def get_invoice_by_portal_token(token):
    """Return a read-only view of an invoice via its portal_token. Public, no auth."""
    try:
        query = f"SELECT * FROM c WHERE c.portal_token = '{token}'"
        items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
        if not items:
            return jsonify({'error': 'Invoice not found or link is invalid'}), 404
        inv = items[0]
        # Return only safe, read-only fields
        safe = {
            'id': inv.get('id'),
            'invoice_number': inv.get('invoice_number'),
            'issue_date': inv.get('issue_date'),
            'due_date': inv.get('due_date'),
            'status': inv.get('status'),
            'customer_name': inv.get('customer_name'),
            'customer_email': inv.get('customer_email'),
            'subtotal': inv.get('subtotal', 0),
            'total_tax': inv.get('total_tax', 0),
            'cgst_amount': inv.get('cgst_amount', 0),
            'sgst_amount': inv.get('sgst_amount', 0),
            'igst_amount': inv.get('igst_amount', 0),
            'total_amount': inv.get('total_amount', 0),
            'amount_paid': inv.get('amount_paid', 0),
            'balance_due': inv.get('balance_due', 0),
            'payment_terms': inv.get('payment_terms', ''),
            'notes': inv.get('notes', ''),
            'terms_conditions': inv.get('terms_conditions', ''),
            'is_gst_applicable': inv.get('is_gst_applicable', False),
            'items': inv.get('items', []),
            'portal_token': token,
        }
        return jsonify(safe), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Generate / regenerate a portal token for an existing invoice ─────────────
@api_blueprint.route('/invoices/<invoice_id>/generate-portal-token', methods=['POST'])
def generate_portal_token(invoice_id):
    """Generate or regenerate a portal_token for an existing invoice."""
    try:
        query = "SELECT * FROM c WHERE c.id = @id"
        items = list(invoices_container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": invoice_id}],
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Invoice not found'}), 404
        inv = items[0]
        if inv.get('tenant_id') != request.tenant_id:
            return jsonify({'error': 'Forbidden'}), 403
        new_token = inv.get('portal_token') or secrets.token_urlsafe(32)
        if not inv.get('portal_token'):
            inv['portal_token'] = new_token
            inv['updated_at'] = datetime.utcnow().isoformat()
            invoices_container.upsert_item(body=inv)
        return jsonify({'portal_token': inv['portal_token']}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Record a payment against an invoice ──────────────────────────────────────
@api_blueprint.route('/invoices/<invoice_id>/record-payment', methods=['POST'])
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
                'required': ['amount', 'payment_mode', 'payment_date'],
                'properties': {
                    'amount':       {'type': 'number',  'description': 'Amount being paid (must be > 0 and <= balance_due)'},
                    'payment_mode': {'type': 'string',  'description': 'e.g. Bank Transfer, Cash, UPI, Cheque'},
                    'payment_date': {'type': 'string',  'format': 'date', 'description': 'Date of payment (YYYY-MM-DD)'},
                    'reference':    {'type': 'string',  'description': 'Transaction / cheque reference number'},
                    'notes':        {'type': 'string',  'description': 'Optional notes'}
                }
            }
        }
    ],
    'responses': {
        '200': {'description': 'Payment recorded successfully'},
        '400': {'description': 'Validation error'},
        '404': {'description': 'Invoice not found'}
    }
})
def record_payment(invoice_id):
    """Record a payment against an invoice, updating amount_paid, balance_due and status."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Validate required fields
    errors = {}
    for f in ['amount', 'payment_mode', 'payment_date']:
        if f not in data:
            errors[f] = f'{f} is required'
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400

    try:
        amount = float(data['amount'])
    except (ValueError, TypeError):
        return jsonify({'error': 'Validation failed', 'details': {'amount': 'Must be a number'}}), 400

    if amount <= 0:
        return jsonify({'error': 'Validation failed', 'details': {'amount': 'Must be greater than zero'}}), 400

    try:
        items = list(invoices_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
            parameters=[
                {"name": "@id",  "value": invoice_id},
                {"name": "@tid", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Invoice not found'}), 404

        inv = items[0]
        before_payment_snapshot = copy.deepcopy(inv)

        if inv.get('status') == 'Cancelled':
            return jsonify({'error': 'Cannot record payment on a cancelled invoice'}), 400

        balance_due = float(inv.get('balance_due', inv.get('total_amount', 0)))
        if amount > balance_due:
            return jsonify({'error': 'Validation failed', 'details': {'amount': f'Exceeds balance due of {balance_due:.2f}'}}), 400

        # Build payment record
        payment_entry = {
            'id':           str(uuid.uuid4()),
            'amount':       round(amount, 2),
            'payment_mode': data['payment_mode'],
            'payment_date': data['payment_date'],
            'reference':    data.get('reference', ''),
            'notes':        data.get('notes', ''),
            'recorded_at':  datetime.utcnow().isoformat(),
            'recorded_by':  request.user_id
        }

        # Append to payment history
        history = inv.get('payment_history', [])
        history.append(payment_entry)

        new_amount_paid = round(float(inv.get('amount_paid', 0)) + amount, 2)
        new_balance_due = round(float(inv.get('total_amount', 0)) - new_amount_paid, 2)
        if new_balance_due < 0:
            new_balance_due = 0.0

        inv['payment_history'] = history
        inv['amount_paid']     = new_amount_paid
        inv['balance_due']     = new_balance_due
        inv['payment_mode']    = data['payment_mode']
        if new_balance_due <= 0:
            inv['status'] = 'Paid'
        elif new_amount_paid > 0:
            inv['status'] = 'Partially Paid'
        inv['updated_at']      = datetime.utcnow().isoformat()

        invoices_container.replace_item(item=inv['id'], body=inv)
        log_audit("payment", "update", invoice_id, before_payment_snapshot, inv,
                  user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)

        if inv['status'] == 'Paid':
            dispatch_webhook_event(
                tenant_id=request.tenant_id,
                event="invoice.paid",
                payload={"invoice_id": inv["id"],
                         "invoice_number": inv.get("invoice_number"),
                         "total": inv.get("total_amount"),
                         "amount_paid": new_amount_paid},
            )
            create_notification(
                tenant_id=request.tenant_id,
                notification_type="payment_received",
                title="Payment Received",
                message=f"Invoice {inv.get('invoice_number', inv['id'])} has been fully paid (₹{new_amount_paid:,.2f}).",
                entity_id=inv["id"],
                entity_type="invoice",
                user_id=getattr(request, 'user_id', None),
            )

        return jsonify({
            'message':      'Payment recorded successfully',
            'payment':      payment_entry,
            'invoice':      sanitize_item(inv)
        }), 200

    except Exception as e:
        return jsonify({'error': f'Failed to record payment: {str(e)}'}), 500


# ── Send invoice email to customer ───────────────────────────────────────────
@api_blueprint.route('/invoices/<invoice_id>/send-email', methods=['POST'])
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
            'required': False,
            'schema': {
                'type': 'object',
                'properties': {
                    'recipient_email': {'type': 'string',  'description': 'Override recipient (defaults to customer_email on the invoice)'},
                    'message':         {'type': 'string',  'description': 'Optional personal message included in the email body'},
                    'attach_pdf':      {'type': 'boolean', 'description': 'Attach a PDF copy of the invoice', 'default': False}
                }
            }
        }
    ],
    'responses': {
        '200': {'description': 'Email sent successfully'},
        '400': {'description': 'Customer email not set or validation error'},
        '404': {'description': 'Invoice not found'},
        '503': {'description': 'Email service not configured'}
    }
})
def send_invoice_email(invoice_id):
    """Send an invoice to the customer via Azure Communication Services."""
    import os
    from azure.communication.email import EmailClient

    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address    = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')

    if not connection_string:
        return jsonify({'error': 'Email service not configured on the server'}), 503

    data       = request.get_json() or {}
    attach_pdf = bool(data.get('attach_pdf', False))

    inv = None  # keep ref so we can stamp email_status on failure
    try:
        results = list(invoices_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
            parameters=[
                {"name": "@id",  "value": invoice_id},
                {"name": "@tid", "value": request.tenant_id}
            ],
            enable_cross_partition_query=True
        ))
        if not results:
            return jsonify({'error': 'Invoice not found'}), 404

        inv = results[0]

        recipient_email = data.get('recipient_email') or inv.get('customer_email', '').strip()
        if not recipient_email:
            return jsonify({'error': 'No recipient email address found on this invoice'}), 400

        customer_name  = inv.get('customer_name', 'Customer')
        invoice_number = inv.get('invoice_number', inv['id'])
        issue_date     = inv.get('issue_date', '')
        due_date       = inv.get('due_date', '')
        total_amount   = inv.get('total_amount', 0)
        balance_due    = inv.get('balance_due', total_amount)
        portal_token   = inv.get('portal_token', '')
        personal_msg   = data.get('message', '')

        # ── Build item rows HTML ──────────────────────────────────────────────
        item_rows_html = ''
        for line in inv.get('items', []):
            item_rows_html += (
                f"<tr>"
                f"<td style='padding:8px;border:1px solid #e0e0e0'>{line.get('name', '')}</td>"
                f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{float(line.get('quantity', 0)):.2f}</td>"
                f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('rate', 0)):,.2f}</td>"
                f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>\u20b9{float(line.get('amount', 0)):,.2f}</td>"
                f"</tr>"
            )

        # ── Fetch tenant branding for email colours ─────────────────────────
        _email_branding = _get_tenant_branding(request.tenant_id)
        _primary  = _email_branding.get('primary_color',  '#2563EB')
        _accent   = _email_branding.get('accent_color',   '#2d6cdf')

        view_link = ''
        if portal_token:
            base_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
            view_link = (
                f"<p style='margin-top:20px'>"
                f"<a href='{base_url}/portal/invoice/{portal_token}' "
                f"style='background:{_primary};color:#fff;padding:10px 20px;border-radius:4px;text-decoration:none'>"
                f"View Invoice Online</a></p>"
            )

        personal_msg_html = f"<p style='color:#475569'>{personal_msg}</p>" if personal_msg else ''

        html_content = f"""
        <html>
        <body style='font-family:Inter,Arial,sans-serif;color:#0F172A;max-width:640px;margin:auto'>
            <div style='background:{_primary};padding:24px;border-radius:8px 8px 0 0'>
                <h2 style='color:#fff;margin:0'>Invoice {invoice_number}</h2>
            </div>
            <div style='background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px'>
                <p>Dear {customer_name},</p>
                {personal_msg_html}
                <p>Please find your invoice details below:</p>
                <table style='width:100%;border-collapse:collapse;margin:16px 0'>
                    <thead>
                        <tr style='background:#F8FAFC'>
                            <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>Item</th>
                            <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Qty</th>
                            <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Rate</th>
                            <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Amount</th>
                        </tr>
                    </thead>
                    <tbody>{item_rows_html}</tbody>
                </table>
                <table style='width:100%;border-collapse:collapse;margin-top:8px'>
                    <tr><td style='padding:4px 8px;color:#475569'>Subtotal</td>
                        <td style='padding:4px 8px;text-align:right'>\u20b9{float(inv.get("subtotal",0)):,.2f}</td></tr>
                    <tr><td style='padding:4px 8px;color:#475569'>Tax</td>
                        <td style='padding:4px 8px;text-align:right'>\u20b9{float(inv.get("total_tax",0)):,.2f}</td></tr>
                    <tr style='font-weight:bold;font-size:16px'>
                        <td style='padding:8px;border-top:2px solid #E2E8F0'>Total</td>
                        <td style='padding:8px;border-top:2px solid #E2E8F0;text-align:right'>\u20b9{float(total_amount):,.2f}</td>
                    </tr>
                    <tr style='color:#D97706'>
                        <td style='padding:4px 8px'>Balance Due</td>
                        <td style='padding:4px 8px;text-align:right;font-weight:bold'>\u20b9{float(balance_due):,.2f}</td>
                    </tr>
                </table>
                <p style='margin-top:16px;color:#475569'>
                    <strong>Issue Date:</strong> {issue_date} &nbsp;|&nbsp;
                    <strong>Due Date:</strong> {due_date}
                </p>
                {view_link}
                <p style='color:#94A3B8;font-size:12px;margin-top:32px'>
                    This is an automated email from Solidev Books.
                </p>
            </div>
        </body>
        </html>
        """

        # ── Build email message ───────────────────────────────────────────────
        email_message = {
            "senderAddress": sender_address,
            "recipients": {"to": [{"address": recipient_email}]},
            "content": {
                "subject": (
                    f"Invoice {invoice_number} "
                    f"from {customer_name if customer_name != 'Customer' else 'us'} "
                    f"\u2014 Due {due_date}"
                ),
                "html": html_content
            }
        }

        # ── Optional PDF attachment ───────────────────────────────────────────
        if attach_pdf:
            try:
                _branding = _get_tenant_branding(request.tenant_id)
                pdf_bytes = build_invoice_pdf(inv, branding=_branding)
                email_message["attachments"] = [{
                    "name":          f"invoice_{invoice_number}.pdf",
                    "contentType":   "application/pdf",
                    "contentInBase64": base64.b64encode(pdf_bytes).decode('utf-8')
                }]
            except Exception as pdf_err:
                print(f"WARNING: PDF generation failed, sending without attachment: {pdf_err}")

        # ── Send via Azure Communication Services ─────────────────────────────
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send(email_message)
        result = poller.result()

        # ── Stamp invoice document ────────────────────────────────────────────
        now = datetime.utcnow().isoformat()
        inv['email_status']  = 'sent'
        inv['email_sent_at'] = now
        inv['last_sent_at']  = now             # backward-compat
        inv['last_sent_to']  = recipient_email
        inv['updated_at']    = now
        if inv.get('status') == 'Draft':       # auto-advance on first send
            inv['status'] = 'Issued'
        invoices_container.replace_item(item=inv['id'], body=inv)

        return jsonify({
            'message':        'Invoice email sent successfully',
            'sent_to':        recipient_email,
            'message_id':     result.get('id'),
            'invoice_status': inv['status'],
            'pdf_attached':   attach_pdf and 'attachments' in email_message
        }), 200

    except Exception as e:
        # Stamp failure on the invoice doc so the UI shows "Failed"
        if inv is not None:
            try:
                inv['email_status']    = 'failed'
                inv['email_failed_at'] = datetime.utcnow().isoformat()
                inv['updated_at']      = datetime.utcnow().isoformat()
                invoices_container.replace_item(item=inv['id'], body=inv)
            except Exception:
                pass  # best-effort — don't mask the original error
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500


# ── GET /invoices/:id/pdf — stream PDF bytes for a specific invoice ───────────
@api_blueprint.route('/invoices/<invoice_id>/pdf', methods=['GET'])
def get_invoice_pdf(invoice_id):
    """Fetch an invoice and return it as a generated PDF file."""
    items = list(invoices_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    inv = items[0]
    if inv.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403

    try:
        _branding = _get_tenant_branding(request.tenant_id)
        pdf_bytes = build_invoice_pdf(inv, branding=_branding)
        inv_number = inv.get('invoice_number', 'invoice').replace('/', '-')
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={inv_number}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


# ── POST /invoices/:id/send-reminder — send a payment reminder email ──────────
@api_blueprint.route('/invoices/<invoice_id>/send-reminder', methods=['POST'])
def send_invoice_reminder(invoice_id):
    """Send a payment reminder email for an outstanding invoice."""
    import os

    items = list(invoices_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    inv = items[0]
    if inv.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403

    body = request.get_json(silent=True) or {}
    recipient_email = (
        body.get('recipient_email')
        or inv.get('customer_email')
    )
    if not recipient_email:
        return jsonify({'error': 'No customer email on invoice. Pass recipient_email in body.'}), 400

    acs_conn = os.getenv('AZURE_COMMUNICATION_CONNECTION_STRING')
    sender   = os.getenv('ACS_SENDER_ADDRESS', 'donotreply@youremaildomain.com')
    if not acs_conn:
        return jsonify({'error': 'Email service not configured on server.'}), 503

    inv_number  = inv.get('invoice_number', '')
    balance_due = float(inv.get('balance_due', inv.get('total_amount', 0)))
    due_date    = inv.get('due_date', 'N/A')

    plain = (
        f"Dear Customer,\n\n"
        f"This is a friendly payment reminder for Invoice {inv_number}.\n"
        f"Balance Due: \u20b9{balance_due:,.2f}\n"
        f"Due Date: {due_date}\n\n"
        f"Please arrange payment at your earliest convenience.\n\n"
        f"Thank you,\nSolidev Books"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#1E40AF">Payment Reminder</h2>
      <p>Dear Customer,</p>
      <p>This is a friendly reminder that Invoice <strong>{inv_number}</strong> has an outstanding balance.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <tr><td style="padding:8px;background:#F8FAFC"><strong>Invoice #</strong></td>
            <td style="padding:8px">{inv_number}</td></tr>
        <tr><td style="padding:8px;background:#F8FAFC"><strong>Balance Due</strong></td>
            <td style="padding:8px;color:#DC2626"><strong>\u20b9{balance_due:,.2f}</strong></td></tr>
        <tr><td style="padding:8px;background:#F8FAFC"><strong>Due Date</strong></td>
            <td style="padding:8px">{due_date}</td></tr>
      </table>
      <p>Please arrange payment at your earliest convenience.</p>
      <p>Thank you,<br/><strong>Solidev Books</strong></p>
    </div>
    """

    try:
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(acs_conn)
        poller = client.begin_send({
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient_email}]},
            "content": {
                "subject": f"Payment Reminder: Invoice {inv_number}",
                "plainText": plain,
                "html": html,
            },
        })
        poller.result()
        return jsonify({'message': 'Reminder sent successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to send reminder: {str(e)}'}), 500

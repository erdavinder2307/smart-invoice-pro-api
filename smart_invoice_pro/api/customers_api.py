from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import customers_container
from smart_invoice_pro.utils.cosmos_client import invoices_container
from smart_invoice_pro.utils.response_sanitizer import sanitize_item, sanitize_items
from smart_invoice_pro.utils.webhook_dispatcher import dispatch_webhook_event
from smart_invoice_pro.utils.notifications import create_notification
from smart_invoice_pro.utils.audit_logger import log_audit
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity
from smart_invoice_pro.utils.bulk_archive_contracts import (
    add_archive_failure,
    add_archive_success,
    finalize_bulk_archive_result,
    init_bulk_archive_result,
)
from smart_invoice_pro.utils.domain_events import record_bulk_archive_completed
from smart_invoice_pro.utils.audit_logger import log_bulk_archive_summary
from smart_invoice_pro.utils.validation_utils import (
    make_error_response, collect_errors,
    validate_required, validate_email as _validate_email,
    validate_gst as _validate_gst, validate_pan as _validate_pan,
    validate_mobile as _validate_mobile,
    VALIDATION_ERROR, NOT_FOUND_ERROR,
)
import copy
import uuid
from flasgger import swag_from
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import jwt
from functools import wraps
import re
import os
import base64

customers_blueprint = Blueprint('customers', __name__)

CUSTOMER_UPLOAD_FOLDER = 'uploads/customer_documents'
os.makedirs(CUSTOMER_UPLOAD_FOLDER, exist_ok=True)


def _is_archived(customer):
    return customer.get('status') == 'ARCHIVED'

# ─── Validation Helpers (kept for backward compatibility with any direct calls) ─
def validate_email(email):
    """Validate email format — returns bool (legacy helper)."""
    return _validate_email(email) is None

def validate_gst_number(gst):
    """Validate GST number format: 22ZZZZZ9999Z9Z9"""
    if not gst:
        return True  # Optional field
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    return re.match(pattern, gst.upper()) is not None

def validate_pan(pan):
    """Validate PAN format: ZZZZZ9999Z"""
    if not pan:
        return True  # Optional field
    pattern = r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
    return re.match(pattern, pan.upper()) is not None

def validate_mobile(mobile):
    """Validate Indian mobile number format"""
    if not mobile:
        return True  # Optional field
    pattern = r'^[6-9]\d{9}$'
    return re.match(pattern, mobile) is not None


def process_customer_documents(documents, customer_id):
    """Persist new base64 documents and keep existing URL documents."""
    if not isinstance(documents, list):
        return []

    processed = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue

        # Existing document reference
        if doc.get('url'):
            processed.append({
                'name': doc.get('name', 'Document'),
                'url': doc.get('url'),
            })
            continue

        # New document upload via base64
        data_uri = doc.get('document_base64')
        filename = doc.get('document_filename', 'document')
        if not data_uri:
            continue

        try:
            raw_data = data_uri.split(',')[1] if ',' in data_uri else data_uri
            file_bytes = base64.b64decode(raw_data)
            safe_filename = secure_filename(f"{customer_id}_{uuid.uuid4().hex}_{filename}")
            file_path = os.path.join(CUSTOMER_UPLOAD_FOLDER, safe_filename)
            with open(file_path, 'wb') as f:
                f.write(file_bytes)

            processed.append({
                'name': filename,
                'url': f"/uploads/customer_documents/{safe_filename}",
            })
        except Exception as ex:
            print(f"Error saving customer document: {str(ex)}")

    return processed

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
        pass  # handled below
    # ── Collect all field errors at once ────────────────────────────────────
    display_name  = (data.get('display_name') or '').strip()
    email_val     = (data.get('email') or '').strip()
    phone_val     = (data.get('phone') or '').strip()
    gst_val       = (data.get('gst_number') or '').strip()
    pan_val       = (data.get('pan') or '').strip()
    mobile_val    = (data.get('mobile') or '').strip()
    customer_type = data.get('customer_type', 'business')
    company_name  = (data.get('company_name') or '').strip()

    company_name_error = None
    if customer_type == 'business' and not company_name:
        company_name_error = 'Company name is required for business customers'

    field_errors = collect_errors(
        display_name=validate_required(display_name, 'Display Name'),
        email=validate_required(email_val, 'Email') or _validate_email(email_val),
        phone=validate_required(phone_val, 'Phone'),
        gst_number=_validate_gst(gst_val),
        pan=_validate_pan(pan_val),
        mobile=_validate_mobile(mobile_val) if mobile_val else None,
        company_name=company_name_error,
    )
    if field_errors:
        return make_error_response(
            VALIDATION_ERROR, "Please fix the highlighted fields", field_errors
        )
    
    now = datetime.utcnow().isoformat()
    customer_uuid = str(uuid.uuid4())
    item = {
        'id': str(uuid.uuid4()),
        'customer_id': customer_uuid,
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
        'gst_number': data.get('gst_number', '').upper() if data.get('gst_number') else '',
        'pan': data.get('pan', '').upper() if data.get('pan') else '',
        'tax_preference': data.get('tax_preference', 'taxable'),
        'currency': data.get('currency', 'INR'),
        'opening_balance': float(data['opening_balance']) if data.get('opening_balance') not in (None, '', False) else 0.0,
        'payment_terms': data.get('payment_terms', 'due_on_receipt'),
        'website_url': data.get('website_url', ''),
        'department': data.get('department', ''),
        'designation': data.get('designation', ''),
        'x_handle': data.get('x_handle', ''),
        'skype': data.get('skype', ''),
        'facebook': data.get('facebook', ''),
        'billing_street': data.get('billing_street', ''),
        'billing_city': data.get('billing_city', ''),
        'billing_state': data.get('billing_state', ''),
        'billing_zip': data.get('billing_zip', ''),
        'billing_country': data.get('billing_country', 'India'),
        'shipping_street': data.get('shipping_street', ''),
        'shipping_city': data.get('shipping_city', ''),
        'shipping_state': data.get('shipping_state', ''),
        'shipping_zip': data.get('shipping_zip', ''),
        'shipping_country': data.get('shipping_country', 'India'),
        'portal_enabled': data.get('portal_enabled', False),
        'documents': process_customer_documents(data.get('documents', []), customer_uuid),
        'contact_persons': data.get('contact_persons', []),
        'custom_fields': data.get('custom_fields', {}),
        'reporting_tags': data.get('reporting_tags', []),
        'remarks': data.get('remarks', ''),
        'status': 'ACTIVE',
        'archived_at': None,
        'archived_by': None,
        'tenant_id': request.tenant_id,
        'created_at': now,
        'updated_at': now
    }
    
    # Hash portal password if provided
    if data.get('portal_enabled') and data.get('portal_password'):
        item['portal_password'] = generate_password_hash(data['portal_password'], method='pbkdf2:sha256', salt_length=16)
    
    # For backward compatibility, also set 'name' and 'address' fields
    item['name'] = item['display_name']
    item['address'] = item['billing_street']
    item['billing_address'] = item['billing_street']  # alias
    item['shipping_address'] = item['shipping_street']  # alias
    
    customers_container.create_item(body=item)
    # Remove password from response for security
    response_item = sanitize_item(item)
    dispatch_webhook_event(
        tenant_id=request.tenant_id,
        event="customer.created",
        payload={"customer_id": item["id"], "name": item.get("display_name"),
                 "email": item.get("email")},
    )
    create_notification(
        tenant_id=request.tenant_id,
        notification_type="customer_created",
        title="Customer Added",
        message=f"{item.get('display_name', 'A new customer')} has been added to your contacts.",
        entity_id=item["id"],
        entity_type="customer",
        user_id=getattr(request, 'user_id', None),
    )
    log_audit("customer", "create", item["id"], None, item,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
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
    lifecycle = str(request.args.get('lifecycle', 'active')).strip().lower()

    query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
    parameters = [{"name": "@tenant_id", "value": request.tenant_id}]

    created_from = request.args.get('created_from')
    created_to = request.args.get('created_to')
    if created_from and created_to:
        query += " AND c.created_at >= @created_from AND c.created_at <= @created_to"
        parameters.extend([
            {"name": "@created_from", "value": created_from},
            {"name": "@created_to", "value": created_to},
        ])

    _ALLOWED_SORT_FIELDS = {'created_at', 'display_name', 'name', 'company_name'}
    sort_by = request.args.get('sort_by', 'display_name')
    sort_order = request.args.get('sort_order', 'asc').upper()
    if sort_by not in _ALLOWED_SORT_FIELDS:
        sort_by = 'display_name'
    if sort_order not in ('ASC', 'DESC'):
        sort_order = 'ASC'

    query += f" ORDER BY c.{sort_by} {sort_order}"

    items = list(customers_container.query_items(
        query=query,
        parameters=parameters,
        enable_cross_partition_query=True
    ))

    if lifecycle == 'archived':
        items = [item for item in items if _is_archived(item)]
    elif lifecycle == 'all':
        items = list(items)
    else:
        items = [item for item in items if not _is_archived(item)]

    return jsonify(sanitize_items(items))

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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(customers_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    if items[0].get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    if _is_archived(items[0]):
        return jsonify({'error': 'Customer not found'}), 404
    
    # Remove password from response for security
    customer = items[0]
    response_item = sanitize_item(customer)
    return jsonify(response_item)


@customers_blueprint.route('/customers/<customer_id>/overview', methods=['GET'])
def get_customer_overview(customer_id):
    """
    Get customer detail with invoice history and financial summary.
    """
    # Fetch customer
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(customers_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    customer = items[0]
    if customer.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    if _is_archived(customer):
        return jsonify({'error': 'Customer not found'}), 404

    # Fetch invoices for this customer
    inv_query = (
        "SELECT c.id, c.invoice_number, c.issue_date, c.due_date, "
        "c.total_amount, c.amount_paid, c.balance_due, c.status "
        "FROM c WHERE c.customer_id = @cid AND c.tenant_id = @tid "
        "ORDER BY c.created_at DESC"
    )
    invoices = list(invoices_container.query_items(
        query=inv_query,
        parameters=[
            {"name": "@cid", "value": customer_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))

    total_invoiced = sum(float(inv.get('total_amount') or 0) for inv in invoices)
    total_paid = sum(float(inv.get('amount_paid') or 0) for inv in invoices)
    outstanding = sum(
        float(inv.get('balance_due') or 0)
        for inv in invoices
        if inv.get('status') not in ('Paid', 'Cancelled', 'Void')
    )

    return jsonify({
        'customer': sanitize_item(customer),
        'invoices': invoices,
        'total_invoiced': round(total_invoiced, 2),
        'total_paid': round(total_paid, 2),
        'outstanding': round(outstanding, 2),
        'invoice_count': len(invoices),
    }), 200


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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(customers_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    before_snapshot = copy.deepcopy(item)
    
    # Validate email format if being updated
    if 'email' in data and not validate_email(data['email']):
        return jsonify({'error': 'Invalid email format'}), 400
    
    # Validate GST number if provided
    if 'gst_number' in data and data['gst_number'] and not validate_gst_number(data['gst_number']):
        return jsonify({'error': 'Invalid GST number format. Expected: 22ZZZZZ9999Z9Z9'}), 400
    
    # Validate PAN if provided
    if 'pan' in data and data['pan'] and not validate_pan(data['pan']):
        return jsonify({'error': 'Invalid PAN format. Expected: ZZZZZ9999Z'}), 400
    
    # Validate mobile if provided
    if 'mobile' in data and data['mobile'] and not validate_mobile(data['mobile']):
        return jsonify({'error': 'Invalid mobile number format'}), 400
    
    # List of updateable fields (using corrected field names)
    updateable_fields = [
        'display_name', 'email', 'phone', 'mobile', 'customer_type', 'salutation', 'first_name', 'last_name',
        'company_name', 'language', 'gst_treatment', 'place_of_supply', 'gst_number', 'pan',
        'tax_preference', 'currency', 'opening_balance', 'payment_terms',
        'website_url', 'department', 'designation', 'x_handle', 'skype', 'facebook',
        'billing_street', 'billing_city', 'billing_state', 'billing_zip', 'billing_country',
        'shipping_street', 'shipping_city', 'shipping_state', 'shipping_zip', 'shipping_country',
        'portal_enabled', 'documents', 'contact_persons', 'custom_fields', 'reporting_tags', 'remarks'
    ]
    
    # Update each field if provided in request
    for field in updateable_fields:
        if field in data:
            if field == 'opening_balance':
                try:
                    item[field] = float(data[field]) if data[field] != '' else 0.0
                except (TypeError, ValueError):
                    item[field] = 0.0
            elif field in ['gst_number', 'pan']:
                # Uppercase GST and PAN
                item[field] = data[field].upper() if data[field] else ''
            elif field == 'documents':
                item[field] = process_customer_documents(data[field], item.get('customer_id', item['id']))
            elif field in ['contact_persons', 'custom_fields', 'reporting_tags']:
                # Handle complex types
                item[field] = data[field]
            else:
                item[field] = data[field]
    
    # Update backward compatibility fields and aliases
    if 'display_name' in data:
        item['name'] = data['display_name']
    if 'billing_street' in data:
        item['address'] = data['billing_street']
        item['billing_address'] = data['billing_street']
    if 'shipping_street' in data:
        item['shipping_address'] = data['shipping_street']
    
    # Handle portal password update if provided
    if data.get('portal_enabled') and data.get('portal_password'):
        item['portal_password'] = generate_password_hash(data['portal_password'], method='pbkdf2:sha256', salt_length=16)
    
    item['updated_at'] = datetime.utcnow().isoformat()
    customers_container.upsert_item(body=item)
    log_audit("customer", "update", customer_id, before_snapshot, item,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
    
    # Remove password from response for security
    response_item = sanitize_item(item)
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
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(customers_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    if _is_archived(item):
        return jsonify({'error': 'Customer not found'}), 404

    dependency = check_entity_dependencies('customer', customer_id, request.tenant_id)
    archived_item = archive_entity(
        customers_container,
        item,
        'customer',
        request.tenant_id,
        user_id=getattr(request, 'user_id', None),
        reason='User requested archive from delete action',
    )

    return jsonify({
        'message': 'Customer archived',
        'status': archived_item.get('status'),
        'dependencySummary': dependency.get('dependencySummary', {}),
    })


@customers_blueprint.route('/customers/bulk-archive', methods=['POST'])
@customers_blueprint.route('/customers/bulk', methods=['POST'])
def bulk_archive_customers():
    """Lifecycle-aware bulk archive for customers."""
    payload = request.get_json() or {}
    ids = payload.get('ids') or []
    action = str(payload.get('action') or 'archive').strip().lower()

    if action not in {'archive', 'delete'}:
        return jsonify({'error': 'Invalid bulk action'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty array'}), 400

    result = init_bulk_archive_result('customer', ids)
    tenant_id = request.tenant_id
    user_id = getattr(request, 'user_id', None)

    for customer_id in ids:
        try:
            rows = list(customers_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
                parameters=[
                    {"name": "@id", "value": customer_id},
                    {"name": "@tenant_id", "value": tenant_id},
                ],
                enable_cross_partition_query=True,
            ))

            if not rows:
                add_archive_failure(result, customer_id, 'NOT_FOUND', 'Customer not found')
                continue

            customer = rows[0]
            deps = check_entity_dependencies('customer', customer_id, tenant_id)

            if _is_archived(customer):
                add_archive_failure(
                    result, customer_id, 'ALREADY_ARCHIVED', 'Customer already archived',
                    dependency_summary=deps.get('dependencySummary', {}),
                )
                continue

            archive_entity(
                customers_container,
                customer,
                'customer',
                tenant_id,
                user_id,
                reason='bulk_archive',
            )
            add_archive_success(
                result, customer_id,
                dependency_summary=deps.get('dependencySummary', {}),
                metadata={'message': 'Customer archived successfully'},
            )
        except Exception as exc:
            add_archive_failure(result, customer_id, 'INTERNAL_ERROR', str(exc))

    finalize_bulk_archive_result(result)
    log_bulk_archive_summary(
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type='customer',
        requested_count=result['requestedCount'],
        success_count=result['successCount'],
        failed_count=result['failedCount'],
        dependency_summary=result.get('dependencySummary', {}),
    )
    record_bulk_archive_completed(tenant_id, user_id, 'customer', result)
    return jsonify(result), 200


@customers_blueprint.route('/customers/<customer_id>/restore', methods=['POST'])
def restore_customer(customer_id):
    """Restore an archived customer back to ACTIVE status."""
    items = list(customers_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    if not _is_archived(item):
        return jsonify({'error': 'Customer is not archived'}), 422
    restored = restore_entity(
        customers_container,
        item,
        'customer',
        request.tenant_id,
        user_id=getattr(request, 'user_id', None),
        reason='User requested restore',
    )
    return jsonify({'message': 'Customer restored', 'status': restored.get('status')}), 200


@customers_blueprint.route('/customers/<customer_id>/dependencies', methods=['GET'])
def get_customer_dependencies(customer_id):
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(customers_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": customer_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404

    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403

    dependency = check_entity_dependencies('customer', customer_id, request.tenant_id)
    return jsonify(dependency), 200

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

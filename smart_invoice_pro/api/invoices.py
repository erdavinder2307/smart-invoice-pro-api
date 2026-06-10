from flask import Blueprint, request, jsonify, make_response
from smart_invoice_pro.utils.cosmos_client import invoices_container, customers_container, get_container
from smart_invoice_pro.utils.response_sanitizer import sanitize_item, sanitize_items
from smart_invoice_pro.utils.webhook_dispatcher import dispatch_webhook_event
from smart_invoice_pro.utils.notifications import create_notification
from smart_invoice_pro.utils.audit_logger import log_audit
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity, LIFECYCLE_ARCHIVED
from smart_invoice_pro.utils.lifecycle_service import apply_lifecycle_action
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
import copy
import uuid
import secrets
import base64
from flasgger import swag_from
from datetime import datetime, timedelta
from enum import Enum
import jwt
from functools import wraps
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding, branding_for_document
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
from smart_invoice_pro.utils.org_tax_mode import get_org_gst_mode, must_suppress_sales_tax, COMPOSITION
from smart_invoice_pro.utils.stock_utils import validate_stock_out

api_blueprint = Blueprint('api', __name__)

class InvoiceStatus(Enum):
    Draft = 'Draft'
    Issued = 'Issued'
    Paid = 'Paid'
    Overdue = 'Overdue'
    Cancelled = 'Cancelled'
    Partially_Paid = 'Partially Paid'


def _is_archived(item):
    return str(item.get('status', '')).upper() == LIFECYCLE_ARCHIVED


def _to_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def _is_meaningful_item(item):
    if not isinstance(item, dict):
        return False
    return bool(str(item.get('name', '')).strip()) \
        or bool(str(item.get('product_name', '')).strip()) \
        or bool(str(item.get('item_name', '')).strip()) \
        or bool(str(item.get('description', '')).strip()) \
        or _to_number(item.get('quantity', 0)) > 0 \
        or _to_number(item.get('rate', 0)) > 0


def _compute_item_totals(items, is_gst_applicable=True):
    normalized = []
    subtotal = 0.0
    item_tax = 0.0

    for item in items:
        qty = max(0.0, _to_number(item.get('quantity', 0)))
        rate = max(0.0, _to_number(item.get('rate', 0)))
        discount = max(0.0, _to_number(item.get('discount', 0)))
        tax_rate = max(0.0, _to_number(item.get('tax', 0)))

        line_base = max(0.0, qty * rate - discount)
        line_tax = (line_base * tax_rate / 100.0) if is_gst_applicable else 0.0
        line_amount = line_base + line_tax

        subtotal += line_base
        item_tax += line_tax

        normalized.append({
            **item,
            'quantity': qty,
            'rate': rate,
            'discount': discount,
            'tax': tax_rate,
            'amount': line_amount,
        })

    return normalized, subtotal, item_tax


def validate_invoice_payload(data):
    errors = {}
    if not isinstance(data, dict):
        return {'payload': 'Invalid JSON payload.'}

    customer_id = data.get('customer_id')
    if customer_id in (None, '', []):
        errors['customer_id'] = 'Customer is required.'
    elif not str(data.get('customer_name') or '').strip():
        errors['customer_name'] = 'Customer name is required.'

    issue_date = _parse_iso_date(data.get('issue_date'))
    due_date = _parse_iso_date(data.get('due_date'))
    if issue_date is None:
        errors['issue_date'] = 'Invoice date must be a valid date (YYYY-MM-DD).'
    if due_date is None:
        errors['due_date'] = 'Due date must be a valid date (YYYY-MM-DD).'
    if issue_date and due_date and due_date < issue_date:
        errors['due_date'] = 'Due date must be on or after invoice date.'

    status = data.get('status')
    if status not in InvoiceStatus._value2member_map_:
        errors['status'] = f'Invalid status: {status}'

    items = data.get('items', [])
    if not isinstance(items, list):
        errors['items'] = 'Items must be an array.'
        items = []

    meaningful_items = [item for item in items if _is_meaningful_item(item)]
    if len(meaningful_items) < 1:
        errors['items'] = 'At least one item is required.'

    for idx, item in enumerate(items):
        if not _is_meaningful_item(item):
            continue
        name = str(item.get('name') or item.get('product_name') or item.get('item_name') or '').strip()
        quantity = _to_number(item.get('quantity', -1))
        rate = _to_number(item.get('rate', -1))
        discount = _to_number(item.get('discount', 0))
        tax = _to_number(item.get('tax', 0))

        if not name:
            errors[f'items[{idx}].name'] = 'Item name is required.'
        if quantity <= 0:
            errors[f'items[{idx}].quantity'] = 'Quantity must be greater than 0.'
        if rate <= 0:
            errors[f'items[{idx}].rate'] = 'Rate must be greater than zero.'
        if discount < 0:
            errors[f'items[{idx}].discount'] = 'Discount cannot be negative.'
        if tax < 0:
            errors[f'items[{idx}].tax'] = 'Tax cannot be negative.'

    return errors

def validate_invoice_patch(data):
    # amount_paid, balance_due, payment_history are controlled exclusively by /record-payment
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
        if k == 'status':
            if v not in InvoiceStatus._value2member_map_:
                errors[k] = f'Invalid status: {v}'
            elif v in ('Paid', 'Partially Paid'):
                errors[k] = ("Cannot set status to 'Paid' or 'Partially Paid' directly. "
                              "Use the /record-payment endpoint.")
        # Optionally add more type checks here
    return errors


# ── Stock helper: create a stock transaction for each line item ───────────────
_STOCK_COMMITTED = {'Issued', 'Partially Paid', 'Paid', 'Overdue'}

def _adjust_stock(items, invoice_number, invoice_id, tenant_id, direction, credit_items=None):
    """Create stock ledger entries for invoice items.
    direction='OUT' decrements stock (invoice activated).
    direction='IN'  reverses stock  (invoice cancelled/voided/deleted).
    credit_items: optional lines to virtually credit before OUT validation.
    Returns (None, None) on success or (message, details) on failure.
    """
    if direction == 'OUT':
        err_msg, err_details = validate_stock_out(items, tenant_id, credit_items=credit_items)
        if err_msg:
            return err_msg, err_details

    try:
        stock_container = get_container("stock", "/product_id")
    except Exception:
        return None, None
    now = datetime.utcnow().isoformat()
    for inv_item in items:
        if not inv_item.get('product_id') or not inv_item.get('quantity'):
            continue
        try:
            stock_container.create_item(body={
                'id':           str(uuid.uuid4()),
                'product_id':   str(inv_item['product_id']),
                'tenant_id':    tenant_id,
                'quantity':     float(inv_item['quantity']),
                'type':         direction,
                'source':       f'Invoice {invoice_number}',
                'reference_id': invoice_id,
                'timestamp':    now,
            })
        except Exception as e:
            print(f"[stock] Failed to adjust stock for product "
                  f"{inv_item.get('product_id')}: {e}")
            return f"Failed to adjust stock: {e}", {'stock': str(e)}
    return None, None

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
    data = request.get_json() or {}

    validation_errors = validate_invoice_payload(data)
    if validation_errors:
        return jsonify({'error': 'Validation failed', 'details': validation_errors}), 400

    # ── Validate customer exists and belongs to this tenant ───────────────────
    cust_rows = list(customers_container.query_items(
        query=("SELECT c.id FROM c WHERE c.id = @cid AND c.tenant_id = @tid "
               "AND (NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false)"),
        parameters=[
            {"name": "@cid", "value": str(data['customer_id'])},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not cust_rows:
        return jsonify({'error': 'Validation failed',
                        'details': {'customer_id': 'Customer not found.'}}), 400

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
    issue_date = data.get('issue_date')
    due_date   = data.get('due_date') or ''
    if not due_date and issue_date:
        try:
            due_date = (datetime.strptime(issue_date, '%Y-%m-%d') + timedelta(days=default_due_days)).strftime('%Y-%m-%d')
        except ValueError:
            due_date = issue_date
    if not due_date:
        due_date = issue_date

    # ── Server-side GST calculation ──────────────────────────────────────────
    # Org registration type is the ceiling: Composition and Unregistered can
    # never charge GST on sales regardless of what the payload says.
    org_gst_mode = get_org_gst_mode(request.tenant_id)
    if must_suppress_sales_tax(request.tenant_id):
        is_gst_applicable = False
    else:
        is_gst_applicable = bool(data.get('is_gst_applicable', False))
    raw_items = data.get('items', [])
    place_of_supply = (data.get('place_of_supply') or '').strip()

    normalized_items, computed_subtotal, computed_item_tax = _compute_item_totals(
        raw_items,
        is_gst_applicable=is_gst_applicable,
    )

    if is_gst_applicable:
        try:
            seller_state = _get_seller_state(request.tenant_id)
            customer_id_str = str(data.get('customer_id', ''))
            customer_state, gst_treatment, customer_pos = _get_customer_state(
                request.tenant_id, customer_id_str
            )
            effective_pos = place_of_supply or customer_pos or customer_state
            gst_result = calculate_gst(
                items=normalized_items,
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
            cgst_amount  = _to_number(data.get('cgst_amount', 0.0))
            sgst_amount  = _to_number(data.get('sgst_amount', 0.0))
            igst_amount  = _to_number(data.get('igst_amount', 0.0))
            total_tax    = computed_item_tax + cgst_amount + sgst_amount + igst_amount
            stored_items = normalized_items
            gst_treatment = data.get('gst_treatment', 'regular')
    else:
        cgst_amount  = 0.0
        sgst_amount  = 0.0
        igst_amount  = 0.0
        total_tax    = 0.0
        stored_items = normalized_items
        gst_treatment = data.get('gst_treatment', 'regular')

    invoice_discount = max(0.0, _to_number(data.get('invoice_discount', 0.0)))
    round_off = _to_number(data.get('round_off', 0.0))
    computed_total = computed_subtotal + total_tax - invoice_discount + round_off
    amount_paid = max(0.0, _to_number(data.get('amount_paid', 0.0)))
    balance_due = computed_total - amount_paid

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
        'subtotal': computed_subtotal,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'igst_amount': igst_amount,
        'total_tax': total_tax,
        'total_amount': computed_total,
        'amount_paid': amount_paid,
        'invoice_discount': invoice_discount,
        'round_off': round_off,
        'balance_due': balance_due,
        'status': data.get('status'),
        'lifecycle_status': 'ACTIVE',
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
    # Commit stock only when the invoice is immediately Issued (not Draft)
    if item['status'] in _STOCK_COMMITTED:
        stock_err, stock_details = _adjust_stock(
            stored_items, item['invoice_number'], item['id'], request.tenant_id, 'OUT'
        )
        if stock_err:
            return jsonify({'error': stock_err, 'details': stock_details or {}}), 400

    invoices_container.create_item(body=item)

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

@api_blueprint.route('/invoices/bulk', methods=['POST'])
@api_blueprint.route('/invoices/bulk-archive', methods=['POST'])
def bulk_invoice_actions():
    """Bulk actions on invoices: archive/delete, mark_paid, send_email."""
    try:
        data = request.get_json(force=True) or {}
        action = data.get('action', '')
        ids = data.get('ids', [])
        if not action or not ids:
            return jsonify({"error": "action and ids are required"}), 400
        allowed_actions = {'archive', 'delete', 'mark_paid', 'send_email'}
        if action not in allowed_actions:
            return jsonify({"error": f"Unknown action: {action}"}), 400

        archive_mode = action in {'archive', 'delete'}

        tenant_id = request.tenant_id
        processed = []
        skipped = []
        now = datetime.utcnow().isoformat()

        for invoice_id in ids:
            try:
                items = list(invoices_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
                    parameters=[
                        {"name": "@id", "value": invoice_id},
                        {"name": "@tenant_id", "value": tenant_id},
                    ],
                    enable_cross_partition_query=True,
                ))
                if not items:
                    skipped.append({"id": invoice_id, "reason": "not_found"})
                    continue
                doc = items[0]

                if archive_mode:
                    if _is_archived(doc):
                        skipped.append({"id": invoice_id, "reason": "already_archived"})
                        continue
                    if float(doc.get('amount_paid', 0)) > 0:
                        skipped.append({"id": invoice_id, "reason": "has_payments"})
                        continue
                    archive_entity(
                        container=invoices_container,
                        item=doc,
                        entity_type="invoice",
                        tenant_id=tenant_id,
                        user_id=getattr(request, 'user_id', None),
                        reason="bulk_archive",
                    )
                    processed.append({"id": invoice_id, "action": "archive"})

                elif action == 'mark_paid':
                    if _is_archived(doc):
                        skipped.append({"id": invoice_id, "reason": "archived"})
                        continue
                    if doc.get('status') == 'Paid':
                        skipped.append({"id": invoice_id, "reason": "already_paid"})
                        continue
                    doc['status'] = 'Paid'
                    doc['amount_paid'] = doc.get('total_amount', 0)
                    doc['balance_due'] = 0.0
                    doc['updated_at'] = now
                    invoices_container.replace_item(item=doc['id'], body=doc)
                    processed.append({"id": invoice_id, "action": "mark_paid"})

                elif action == 'send_email':
                    # Fire-and-forget placeholder; real email handled by dedicated endpoint
                    processed.append({"id": invoice_id, "action": "send_email"})

            except Exception as item_err:
                skipped.append({"id": invoice_id, "reason": str(item_err)})

        return jsonify({
            "processed": processed,
            "skipped": skipped,
            "success_count": len(processed),
            "failure_count": len(skipped),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Bulk action failed: {str(e)}"}), 500


@api_blueprint.route('/invoices', methods=['GET'])
def list_invoices():
    """List invoices with search, filtering, sorting, pagination and summary meta."""
    try:
        tenant_id = request.tenant_id
        status_filter = request.args.get('status')
        lifecycle = (request.args.get('lifecycle') or 'active').strip().lower()
        search_query = (request.args.get('q') or '').strip()
        date_range = (request.args.get('date_range') or '').strip().lower()
        date_from = (request.args.get('date_from') or '').strip()
        date_to = (request.args.get('date_to') or '').strip()
        min_amount = request.args.get('min_amount')
        max_amount = request.args.get('max_amount')
        include_meta = str(request.args.get('include_meta', '')).lower() in ('1', 'true', 'yes')

        _ALLOWED_SORT_FIELDS = {'created_at', 'issue_date', 'due_date', 'invoice_number', 'total_amount', 'balance_due'}
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'created_at'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get('page_size', 10))
        except ValueError:
            page_size = 10
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        where = ["c.tenant_id = @tenant_id"]
        parameters = [{"name": "@tenant_id", "value": tenant_id}]

        if lifecycle == 'archived':
            where.append("UPPER(c.status) = @archived_status")
            parameters.append({"name": "@archived_status", "value": LIFECYCLE_ARCHIVED})
        elif lifecycle != 'all':
            where.append("(NOT IS_DEFINED(c.status) OR UPPER(c.status) != @archived_status)")
            parameters.append({"name": "@archived_status", "value": LIFECYCLE_ARCHIVED})

        # Snapshot base conditions (no status filter) for overdue_count calculation
        base_where = list(where)
        base_parameters = list(parameters)

        if status_filter:
            if status_filter.lower() == 'overdue':
                # Match dashboard logic: status='Overdue' OR (open status AND due_date < today)
                today_iso = datetime.utcnow().date().isoformat()
                where.append(
                    "(c.status = 'Overdue' OR "
                    "(c.status IN ('Issued', 'Partially Paid') AND c.due_date < @overdue_today))"
                )
                parameters.append({"name": "@overdue_today", "value": today_iso})
            else:
                where.append("c.status = @status")
                parameters.append({"name": "@status", "value": status_filter})

        if search_query:
            where.append(
                "(CONTAINS(LOWER(c.invoice_number), @q) OR CONTAINS(LOWER(c.customer_name), @q))"
            )
            parameters.append({"name": "@q", "value": search_query.lower()})

        if date_range:
            today = datetime.utcnow().date()
            start_date = None
            end_date = None

            if date_range == 'this_week':
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif date_range == 'this_month':
                start_date = today.replace(day=1)
                if start_date.month == 12:
                    next_month = start_date.replace(year=start_date.year + 1, month=1, day=1)
                else:
                    next_month = start_date.replace(month=start_date.month + 1, day=1)
                end_date = next_month - timedelta(days=1)
            elif date_range == 'this_quarter':
                quarter_start_month = ((today.month - 1) // 3) * 3 + 1
                start_date = today.replace(month=quarter_start_month, day=1)
                if quarter_start_month == 10:
                    next_quarter = start_date.replace(year=start_date.year + 1, month=1, day=1)
                else:
                    next_quarter = start_date.replace(month=quarter_start_month + 3, day=1)
                end_date = next_quarter - timedelta(days=1)
            elif date_range == 'this_year':
                start_date = today.replace(month=1, day=1)
                end_date = today.replace(month=12, day=31)
            elif date_range == 'custom':
                if date_from:
                    start_date = datetime.fromisoformat(date_from).date()
                if date_to:
                    end_date = datetime.fromisoformat(date_to).date()

            if start_date:
                where.append("c.issue_date >= @date_from")
                parameters.append({"name": "@date_from", "value": start_date.isoformat()})
            if end_date:
                where.append("c.issue_date <= @date_to")
                parameters.append({"name": "@date_to", "value": end_date.isoformat()})

        if min_amount not in (None, ''):
            where.append("c.total_amount >= @min_amount")
            parameters.append({"name": "@min_amount", "value": float(min_amount)})
        if max_amount not in (None, ''):
            where.append("c.total_amount <= @max_amount")
            parameters.append({"name": "@max_amount", "value": float(max_amount)})

        where_sql = " AND ".join(where)
        base_query = f"SELECT * FROM c WHERE {where_sql}"

        legacy_mode = not include_meta and not any([
            request.args.get('page'),
            request.args.get('page_size'),
            search_query,
            date_range,
            min_amount,
            max_amount,
        ])

        if legacy_mode:
            query = f"{base_query} ORDER BY c.{sort_by} {sort_order}"
            items = list(invoices_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            ))
            return jsonify(sanitize_items(items))

        query = f"{base_query} ORDER BY c.{sort_by} {sort_order} OFFSET {offset} LIMIT {page_size}"
        items = list(invoices_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        ))

        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
        total_items = list(invoices_container.query_items(
            query=count_query,
            parameters=parameters,
            enable_cross_partition_query=True,
        ))
        total = int(total_items[0]) if total_items else 0

        summary = {}
        base_where_sql = " AND ".join(base_where)
        for status_name in InvoiceStatus._value2member_map_.keys():
            s_params = [*base_parameters, {"name": "@summary_status", "value": status_name}]
            s_query = f"SELECT VALUE COUNT(1) FROM c WHERE {base_where_sql} AND c.status = @summary_status"
            s_result = list(invoices_container.query_items(
                query=s_query,
                parameters=s_params,
                enable_cross_partition_query=True,
            ))
            summary[status_name] = int(s_result[0]) if s_result else 0

        # Effective overdue count: matches dashboard logic (due_date < today AND open status)
        oc_today = datetime.utcnow().date().isoformat()
        oc_params = [*base_parameters, {"name": "@oc_today", "value": oc_today}]
        oc_where_sql = base_where_sql + " AND (c.status = 'Overdue' OR (c.status IN ('Issued', 'Partially Paid') AND c.due_date < @oc_today))"
        oc_result = list(invoices_container.query_items(
            query=f"SELECT VALUE COUNT(1) FROM c WHERE {oc_where_sql}",
            parameters=oc_params,
            enable_cross_partition_query=True,
        ))
        summary['overdue_count'] = int(oc_result[0]) if oc_result else 0

        return jsonify({
            "items": sanitize_items(items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": summary,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch invoices: {str(e)}"}), 500


@api_blueprint.route('/invoices/export', methods=['GET'])
def export_invoices_csv():
    """Export invoices as a CSV file. Accepts same filter params as list endpoint."""
    import csv
    import io as _io
    try:
        tenant_id = request.tenant_id
        status_filter = request.args.get('status')
        lifecycle = (request.args.get('lifecycle') or 'active').strip().lower()
        search_query = (request.args.get('q') or '').strip()
        date_range = (request.args.get('date_range') or '').strip().lower()
        date_from = (request.args.get('date_from') or '').strip()
        date_to = (request.args.get('date_to') or '').strip()

        where = ["c.tenant_id = @tenant_id"]
        parameters = [{"name": "@tenant_id", "value": tenant_id}]

        if lifecycle == 'archived':
            where.append("UPPER(c.status) = @archived_status")
            parameters.append({"name": "@archived_status", "value": LIFECYCLE_ARCHIVED})
        elif lifecycle != 'all':
            where.append("(NOT IS_DEFINED(c.status) OR UPPER(c.status) != @archived_status)")
            parameters.append({"name": "@archived_status", "value": LIFECYCLE_ARCHIVED})

        if status_filter:
            where.append("c.status = @status")
            parameters.append({"name": "@status", "value": status_filter})

        if search_query:
            where.append(
                "(CONTAINS(LOWER(c.invoice_number), @q) OR CONTAINS(LOWER(c.customer_name), @q))"
            )
            parameters.append({"name": "@q", "value": search_query.lower()})

        if date_range:
            today = datetime.utcnow().date()
            start_date = None
            end_date = None
            if date_range == 'this_week':
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif date_range == 'this_month':
                start_date = today.replace(day=1)
                next_month = (start_date.replace(month=start_date.month % 12 + 1, day=1)
                              if start_date.month < 12 else start_date.replace(year=start_date.year + 1, month=1, day=1))
                end_date = next_month - timedelta(days=1)
            elif date_range == 'this_year':
                start_date = today.replace(month=1, day=1)
                end_date = today.replace(month=12, day=31)
            elif date_range == 'custom':
                if date_from:
                    start_date = datetime.fromisoformat(date_from).date()
                if date_to:
                    end_date = datetime.fromisoformat(date_to).date()
            if start_date:
                where.append("c.issue_date >= @date_from")
                parameters.append({"name": "@date_from", "value": start_date.isoformat()})
            if end_date:
                where.append("c.issue_date <= @date_to")
                parameters.append({"name": "@date_to", "value": end_date.isoformat()})

        where_sql = " AND ".join(where)
        query = f"SELECT * FROM c WHERE {where_sql} ORDER BY c.issue_date DESC"
        items = list(invoices_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        ))

        output = _io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Invoice #", "Customer", "Issue Date", "Due Date", "Status",
            "Subtotal", "Tax", "Total", "Amount Paid", "Balance Due"
        ])
        for inv in items:
            writer.writerow([
                inv.get("invoice_number", ""),
                inv.get("customer_name", ""),
                inv.get("issue_date", ""),
                inv.get("due_date", ""),
                inv.get("status", ""),
                inv.get("subtotal", 0),
                inv.get("total_tax", 0),
                inv.get("total_amount", 0),
                inv.get("amount_paid", 0),
                inv.get("balance_due", 0),
            ])

        csv_data = output.getvalue()
        response = make_response(csv_data)
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=invoices-export.csv"
        return response

    except Exception as e:
        return jsonify({"error": f"Failed to export invoices: {str(e)}"}), 500


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
    if _is_archived(items[0]):
        return jsonify({'error': 'Invoice not found'}), 404
    return jsonify(sanitize_item(items[0]))


@api_blueprint.route('/invoices/<invoice_id>/dependencies', methods=['GET'])
def get_invoice_dependencies(invoice_id):
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(invoices_container.query_items(
        query=query,
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404

    invoice = items[0]
    if invoice.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403

    dependencies = check_entity_dependencies('invoice', invoice_id, request.tenant_id)
    return jsonify(dependencies), 200

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
    data = request.get_json() or {}

    # ── Payment integrity: strip fields controlled exclusively by /record-payment ─
    _PAYMENT_LOCKED = ('amount_paid', 'balance_due', 'payment_history')
    data = {k: v for k, v in data.items() if k not in _PAYMENT_LOCKED}
    # Prevent manual status jump to Paid/Partially Paid without a payment record
    if data.get('status') in ('Paid', 'Partially Paid'):
        return jsonify({'error': 'Validation failed', 'details': {
            'status': ("Cannot set status to 'Paid' or 'Partially Paid' directly. "
                       "Use the /record-payment endpoint.")
        }}), 400

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
    if _is_archived(item):
        return jsonify({'error': 'Invoice not found'}), 404

    merged_payload = {
        **item,
        **data,
        'items': data.get('items', item.get('items', [])),
    }
    validation_errors = validate_invoice_payload(merged_payload)
    if validation_errors:
        return jsonify({'error': 'Validation failed', 'details': validation_errors}), 400

    # Enforce org-level GST mode — Composition/Unregistered cannot charge GST on sales
    if must_suppress_sales_tax(request.tenant_id):
        is_gst_applicable = False
    else:
        is_gst_applicable = bool(merged_payload.get('is_gst_applicable', False))
    normalized_items, computed_subtotal, computed_item_tax = _compute_item_totals(
        merged_payload.get('items', []),
        is_gst_applicable=is_gst_applicable,
    )
    cgst_amount = _to_number(merged_payload.get('cgst_amount', 0.0)) if is_gst_applicable else 0.0
    sgst_amount = _to_number(merged_payload.get('sgst_amount', 0.0)) if is_gst_applicable else 0.0
    igst_amount = _to_number(merged_payload.get('igst_amount', 0.0)) if is_gst_applicable else 0.0
    manual_tax = cgst_amount + sgst_amount + igst_amount
    computed_total_tax = computed_item_tax + (manual_tax if is_gst_applicable else 0.0)
    invoice_discount = max(0.0, _to_number(merged_payload.get('invoice_discount', 0.0)))
    round_off = _to_number(merged_payload.get('round_off', 0.0))
    computed_total = computed_subtotal + computed_total_tax - invoice_discount + round_off
    amount_paid = max(0.0, _to_number(merged_payload.get('amount_paid', 0.0)))
    balance_due = computed_total - amount_paid

    before_snapshot = copy.deepcopy(item)
    # Update all fields from the request (PUT = full replacement)
    for field in [
        'invoice_number', 'customer_id', 'customer_name', 'customer_email', 'customer_phone',
        'issue_date', 'due_date', 'payment_terms',
        'subtotal', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_tax',
        'total_amount', 'amount_paid', 'balance_due', 'status', 'payment_mode',
        'notes', 'terms_conditions', 'is_gst_applicable', 'invoice_type',
        'subject', 'salesperson', 'place_of_supply', 'gst_treatment',
        'created_at', 'updated_at'
    ]:
        if field in data:
            item[field] = data[field]

    item['items'] = normalized_items
    item['subtotal'] = computed_subtotal
    item['total_tax'] = computed_total_tax
    item['total_amount'] = computed_total
    item['invoice_discount'] = invoice_discount
    item['round_off'] = round_off
    item['amount_paid'] = amount_paid
    item['balance_due'] = balance_due
    item['updated_at'] = datetime.utcnow().isoformat()

    # ── Stock transition based on status change ───────────────────────────────
    _old_status = before_snapshot.get('status', 'Draft')
    _new_status = item.get('status', _old_status)
    _old_committed = _old_status in _STOCK_COMMITTED
    _new_committed = _new_status in _STOCK_COMMITTED
    _inv_num = item.get('invoice_number', '')
    if not _old_committed and _new_committed:
        stock_err, stock_details = _adjust_stock(
            normalized_items, _inv_num, invoice_id, request.tenant_id, 'OUT'
        )
        if stock_err:
            return jsonify({'error': stock_err, 'details': stock_details or {}}), 400
    elif _old_committed and not _new_committed:
        _adjust_stock(before_snapshot.get('items', []), _inv_num, invoice_id, request.tenant_id, 'IN')
    elif _old_committed and _new_committed and (
        before_snapshot.get('items') != normalized_items
    ):
        stock_err, stock_details = _adjust_stock(
            normalized_items,
            _inv_num,
            invoice_id,
            request.tenant_id,
            'OUT',
            credit_items=before_snapshot.get('items', []),
        )
        if stock_err:
            return jsonify({'error': stock_err, 'details': stock_details or {}}), 400
        _adjust_stock(before_snapshot.get('items', []), _inv_num, invoice_id, request.tenant_id, 'IN')
        _adjust_stock(normalized_items, _inv_num, invoice_id, request.tenant_id, 'OUT')

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
    if _is_archived(item):
        return jsonify({'message': 'Invoice already archived'}), 200

    # ── Block archiving invoices that have recorded payments ──────────────────
    if float(item.get('amount_paid', 0)) > 0:
        return jsonify({'error': (
            'Cannot archive an invoice with recorded payments. '
            'Void or reverse the payments first.'
        )}), 409

    # Reverse stock for committed invoices before archiving
    if item.get('status') in _STOCK_COMMITTED:
        _adjust_stock(item.get('items', []), item.get('invoice_number', ''),
                      invoice_id, request.tenant_id, 'IN')

    lifecycle_result = apply_lifecycle_action(
        container=invoices_container,
        item=item,
        entity_type='invoice',
        tenant_id=request.tenant_id,
        user_id=getattr(request, 'user_id', None),
        requested_action='delete',
        reason='User requested delete',
    )

    return jsonify({
        'message': 'Invoice archived successfully',
        'performedAction': lifecycle_result.get('performedAction'),
        'status': lifecycle_result.get('status'),
        'dependencySummary': lifecycle_result.get('dependencySummary', {}),
        'hardDeleteAllowed': lifecycle_result.get('hardDeleteAllowed', False),
    })


@api_blueprint.route('/invoices/<invoice_id>/restore', methods=['POST'])
def restore_invoice(invoice_id):
    """Restore an archived invoice back to its previous active status."""
    items = list(invoices_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": invoice_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({'error': 'Invoice not found'}), 404
    item = items[0]
    if item.get('tenant_id') != request.tenant_id:
        return jsonify({'error': 'Forbidden'}), 403
    if not _is_archived(item):
        return jsonify({'error': 'Invoice is not archived'}), 422
    restored = restore_entity(
        invoices_container,
        item,
        'invoice',
        request.tenant_id,
        user_id=getattr(request, 'user_id', None),
        reason='User requested restore',
    )
    return jsonify({'message': 'Invoice restored', 'status': restored.get('status')}), 200


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
    if _is_archived(item):
        return jsonify({'error': 'Invoice not found'}), 404

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
        # Parameterized query — never interpolate token directly (injection risk)
        items = list(invoices_container.query_items(
            query="SELECT * FROM c WHERE c.portal_token = @token",
            parameters=[{"name": "@token", "value": token}],
            enable_cross_partition_query=True,
        ))
        if not items:
            return jsonify({'error': 'Invoice not found or link is invalid'}), 404
        inv = items[0]

        # Resolve tenant branding for the customer portal (no staff JWT needed)
        portal_branding = {}
        tenant_id = inv.get('tenant_id')
        if tenant_id:
            try:
                from smart_invoice_pro.api.organization_profile_api import _get_profile
                from smart_invoice_pro.api.branding_api import _extract_branding
                profile = _get_profile(tenant_id)
                b = _extract_branding(profile)
                from smart_invoice_pro.utils.org_tax_mode import derive_gst_mode
                _gst_mode = derive_gst_mode(
                    profile.get('gst_registration_type', 'regular'),
                    profile.get('gst_enabled'),
                )
                portal_branding = {
                    'primary_color':    b.get('primary_color', '#2563EB'),
                    'accent_color':     b.get('accent_color', '#2d6cdf'),
                    'logo_url':         b.get('logo_url', ''),
                    'organization_name': (profile.get('organization_name') or '').strip(),
                    'gst_mode':          _gst_mode,
                    'gstin':             (profile.get('gstin') or '').strip(),
                }
            except Exception:
                pass

        safe = {
            'id':                inv.get('id'),
            'invoice_number':    inv.get('invoice_number'),
            'issue_date':        inv.get('issue_date'),
            'due_date':          inv.get('due_date'),
            'status':            inv.get('status'),
            'customer_name':     inv.get('customer_name'),
            'customer_email':    inv.get('customer_email'),
            'subtotal':          inv.get('subtotal', 0),
            'total_tax':         inv.get('total_tax', 0),
            'cgst_amount':       inv.get('cgst_amount', 0),
            'sgst_amount':       inv.get('sgst_amount', 0),
            'igst_amount':       inv.get('igst_amount', 0),
            'total_amount':      inv.get('total_amount', 0),
            'amount_paid':       inv.get('amount_paid', 0),
            'balance_due':       inv.get('balance_due', 0),
            'payment_terms':     inv.get('payment_terms', ''),
            'notes':             inv.get('notes', ''),
            'terms_conditions':  inv.get('terms_conditions', ''),
            'is_gst_applicable': inv.get('is_gst_applicable', False),
            'items':             inv.get('items', []),
            'portal_token':      token,
            'branding':          portal_branding,
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


# ── Void (cancel) an invoice ─────────────────────────────────────────────────
@api_blueprint.route('/invoices/<invoice_id>/void', methods=['POST'])
def void_invoice(invoice_id):
    """Void an issued invoice: sets status to Cancelled with a mandatory reason."""
    data = request.get_json() or {}
    reason = str(data.get('reason', '')).strip()
    if not reason:
        return jsonify({'error': 'Validation failed', 'details': {'reason': 'Reason is required'}}), 400

    VOIDABLE_STATUSES = {'Issued', 'Partially Paid', 'Overdue'}

    try:
        items = list(invoices_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": invoice_id}],
            enable_cross_partition_query=True,
        ))
        if not items:
            return jsonify({'error': 'Invoice not found'}), 404

        inv = items[0]
        if inv.get('tenant_id') != request.tenant_id:
            return jsonify({'error': 'Forbidden'}), 403

        before_snapshot = copy.deepcopy(inv)

        if inv.get('status') not in VOIDABLE_STATUSES:
            return jsonify({
                'error': (
                    f"Invoice cannot be voided. Current status: {inv.get('status')}. "
                    f"Only {', '.join(sorted(VOIDABLE_STATUSES))} invoices can be voided."
                )
            }), 409

        inv['status']     = 'Cancelled'
        inv['void_reason'] = reason
        inv['voided_at']  = datetime.utcnow().isoformat()
        inv['voided_by']  = getattr(request, 'user_id', None)
        inv['updated_at'] = datetime.utcnow().isoformat()

        # Reverse stock committed when the invoice was issued
        _adjust_stock(inv.get('items', []), inv.get('invoice_number', ''),
                      invoice_id, request.tenant_id, 'IN')

        invoices_container.replace_item(item=inv['id'], body=inv)

        try:
            log_audit(
                'invoice', 'void', invoice_id,
                before={'status': before_snapshot.get('status')},
                after={'status': 'Cancelled', 'void_reason': reason},
                user_id=getattr(request, 'user_id', None),
                tenant_id=request.tenant_id,
            )
        except Exception:
            pass  # Non-critical: void already succeeded

        return jsonify({
            'message':    'Invoice voided successfully',
            'invoice_id': invoice_id,
            'status':     'Cancelled',
        }), 200

    except Exception as e:
        return jsonify({'error': f'Failed to void invoice: {str(e)}'}), 500


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
        if _is_archived(inv):
            return jsonify({'error': 'Archived invoices cannot be emailed'}), 409

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

        # ── Fetch tenant branding for email ──────────────────────────────────
        _email_branding = _get_tenant_branding(request.tenant_id)

        portal_url = ''
        if portal_token:
            base_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
            portal_url = f"{base_url}/portal/invoice/{portal_token}"

        from smart_invoice_pro.services.email_template_service import render_branded_email
        html_content, _plain_content = render_branded_email(
            doc_type='invoice',
            context={
                'doc_number':    invoice_number,
                'customer_name': customer_name,
                'issue_date':    issue_date,
                'due_date':      due_date,
                'total_amount':  total_amount,
                'balance_due':   balance_due,
                'subtotal':      float(inv.get('subtotal', 0)),
                'total_tax':     float(inv.get('total_tax', 0)),
                'items':         inv.get('items', []),
                'message':       personal_msg,
                'portal_url':    portal_url,
            },
            branding=_email_branding,
        )

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
                pdf_bytes = build_invoice_pdf(inv, branding=_branding, doc_type='invoice',
                                             gst_mode=get_org_gst_mode(request.tenant_id))
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

        # ── Brand snapshot: record branding at send time ──────────────────────
        # Only captured once (on first send). Re-sending does not overwrite so
        # that re-generated PDFs continue to match the originally-issued version.
        if not inv.get('brand_snapshot'):
            inv['brand_snapshot'] = {
                'primary_color':     _email_branding.get('primary_color', ''),
                'accent_color':      _email_branding.get('accent_color', ''),
                'secondary_color':   _email_branding.get('secondary_color', ''),
                'logo_url':          _email_branding.get('logo_url', ''),
                'organization_name': _email_branding.get('organization_name', ''),
                'snapshotted_at':    now,
            }

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
        _branding = branding_for_document(inv, request.tenant_id)
        pdf_bytes = build_invoice_pdf(inv, branding=_branding, doc_type='invoice',
                                     gst_mode=get_org_gst_mode(request.tenant_id))
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
    if _is_archived(inv):
        return jsonify({'error': 'Archived invoices cannot receive reminders'}), 409

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

    _reminder_branding = _get_tenant_branding(inv.get('tenant_id') or request.tenant_id)

    from smart_invoice_pro.services.email_template_service import render_reminder_email
    html, plain = render_reminder_email(
        context={
            'doc_number':    inv_number,
            'customer_name': inv.get('customer_name', 'Customer'),
            'balance_due':   balance_due,
            'due_date':      due_date,
        },
        branding=_reminder_branding,
    )

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

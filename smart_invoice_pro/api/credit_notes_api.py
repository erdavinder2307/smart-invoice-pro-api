"""
Credit and debit notes against invoices (Section 34 CGST Act, Rule 53).

    POST   /api/credit-notes                 issue a note against an invoice
    GET    /api/credit-notes                 list (filters: invoice_id, customer_id, status, note_type)
    GET    /api/credit-notes/next-number     preview the next note number
    GET    /api/credit-notes/<id>            one note
    GET    /api/credit-notes/<id>/pdf        the note as a PDF
    POST   /api/credit-notes/<id>/cancel     cancel (reverses its effect on the invoice)
    POST   /api/credit-notes/<id>/refund     record a refund of unapplied credit
    DELETE /api/credit-notes/<id>            refused: notes are cancelled, never deleted

Permissions follow invoices (invoices:create / view / edit): a note is a
correction to an invoice and whoever may raise invoices may correct them.
Every write is organisation-scoped and audit-logged, as invoices are.

The rules themselves live in services/credit_notes.py; this module only
loads documents, calls them and persists the result.
"""
import copy
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, make_response, request

from smart_invoice_pro.api.invoice_generation import branding_for_document, build_invoice_pdf
from smart_invoice_pro.api.invoice_preferences_api import (
    DEFAULT_PREFS, _get_prefs, claim_next_number, peek_next_number,
)
from smart_invoice_pro.api.tax_rates_api import _get_seller_state
from smart_invoice_pro.services.credit_notes import (
    NOTE_TYPES, REASONS, apply_note, build_note_lines, invoice_tax_type, is_live,
    limit_errors, note_issues, note_totals, parse_iso_date, reverse_note,
)
from smart_invoice_pro.utils.audit_logger import log_audit, log_audit_event
from smart_invoice_pro.utils.cosmos_client import (
    credit_notes_container, customers_container, invoices_container, products_container,
)
from smart_invoice_pro.utils.demo_guard import enforce_demo_create_limit
from smart_invoice_pro.utils.org_tax_mode import get_org_gst_mode
from smart_invoice_pro.utils.permission_checker import require_permission
from smart_invoice_pro.utils.response_sanitizer import sanitize_item, sanitize_items
from smart_invoice_pro.utils.webhook_dispatcher import dispatch_webhook_event

credit_notes_blueprint = Blueprint('credit_notes', __name__)

# Invoice statuses a note may be raised against: issued and not cancelled.
NOTEABLE_STATUSES = {'Issued', 'Partially Paid', 'Paid', 'Overdue'}


def _now():
    return datetime.utcnow().isoformat()


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _load_invoice(invoice_id, tenant_id):
    rows = list(invoices_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[{"name": "@id", "value": str(invoice_id)}, {"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    return rows[0] if rows else None


def _load_note(note_id, tenant_id):
    rows = list(credit_notes_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[{"name": "@id", "value": str(note_id)}, {"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    return rows[0] if rows else None


def _notes_for_invoice(invoice_id, tenant_id):
    return list(credit_notes_container.query_items(
        query="SELECT * FROM c WHERE c.invoice_id = @iid AND c.tenant_id = @tid",
        parameters=[{"name": "@iid", "value": str(invoice_id)}, {"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))


def _customer_and_products(customer_id, tenant_id):
    """The customer and the tenant's products by name, for HSN codes and the Rule 53 checks."""
    customer, products = {}, {}
    try:
        params = [{"name": "@tid", "value": tenant_id}]
        rows = list(customers_container.query_items(
            query="SELECT * FROM c WHERE c.id = @cid AND c.tenant_id = @tid",
            parameters=params + [{"name": "@cid", "value": str(customer_id)}],
            enable_cross_partition_query=True,
        ))
        customer = rows[0] if rows else {}
        prods = list(products_container.query_items(
            query="SELECT c.name, c.hsn_sac, c.unit FROM c WHERE c.tenant_id = @tid",
            parameters=params, enable_cross_partition_query=True,
        ))
        products = {str(p.get('name') or '').strip().lower(): p for p in prods}
    except Exception as exc:
        current_app.logger.warning("credit note lookups failed tenant_id=%s: %s", tenant_id, type(exc).__name__)
    return customer, products


def _save_invoice(invoice):
    invoices_container.replace_item(item=invoice['id'], body=invoice)


# ── Create ────────────────────────────────────────────────────────────────────
@credit_notes_blueprint.route('/credit-notes', methods=['POST'])
@require_permission('invoices', 'create')
@enforce_demo_create_limit('credit_note')
def create_credit_note():
    """Issue a credit (or debit) note against one of the organisation's invoices.

    Body: invoice_id, note_type ('credit' | 'debit', default credit), note_date (YYYY-MM-DD,
    default today), reason (see REASONS), reason_note, items [{invoice_line_index, quantity?,
    rate?, discount?}], round_off?, apply_to_invoice (default true), note_number (only when
    auto-numbering is off), notes.
    Returns the note with `gst_warnings` (Rule 53(1A) and Section 34(2)); warnings never block.
    """
    data = request.get_json(silent=True) or {}
    tenant_id = request.tenant_id
    errors = {}

    note_type = str(data.get('note_type') or 'credit').strip().lower()
    if note_type not in NOTE_TYPES:
        errors['note_type'] = "note_type must be 'credit' or 'debit'."
    reason = str(data.get('reason') or '').strip()
    if reason not in REASONS:
        errors['reason'] = 'Reason is required: ' + ', '.join(sorted(REASONS)) + '.'
    note_date = str(data.get('note_date') or datetime.utcnow().strftime('%Y-%m-%d'))
    if parse_iso_date(note_date) is None:
        errors['note_date'] = 'Note date must be a valid date (YYYY-MM-DD).'
    if not data.get('invoice_id'):
        errors['invoice_id'] = 'invoice_id is required.'
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400

    invoice = _load_invoice(data['invoice_id'], tenant_id)
    if invoice is None:
        return jsonify({'error': 'Validation failed', 'details': {'invoice_id': 'Invoice not found.'}}), 400
    if str(invoice.get('status') or '') not in NOTEABLE_STATUSES or \
            str(invoice.get('lifecycle_status') or '').upper() == 'ARCHIVED':
        return jsonify({'error': 'Validation failed', 'details': {
            'invoice_id': f"A note can only be raised against an issued invoice (status is "
                          f"{invoice.get('status')}).",
        }}), 400
    invoice_date = parse_iso_date(invoice.get('issue_date'))
    if invoice_date and parse_iso_date(note_date) < invoice_date:
        return jsonify({'error': 'Validation failed', 'details': {
            'note_date': 'Note date cannot be before the invoice date.'}}), 400

    customer, products_by_name = _customer_and_products(invoice.get('customer_id'), tenant_id)
    try:
        seller_state = _get_seller_state(tenant_id)
    except Exception:
        seller_state = ''
    tax_type = invoice_tax_type(invoice, seller_state)

    lines, line_errors = build_note_lines(invoice, data.get('items'), products_by_name, tax_type)
    if line_errors:
        return jsonify({'error': 'Validation failed', 'details': line_errors}), 400
    totals = note_totals(lines, _num(data.get('round_off'), 0.0))
    if note_type == 'credit':
        existing = _notes_for_invoice(invoice['id'], tenant_id)
        cap_errors = limit_errors(invoice, lines, totals, existing)
        if cap_errors:
            return jsonify({'error': 'Validation failed', 'details': cap_errors}), 400

    prefs = _get_prefs(tenant_id)
    auto_gen = bool(prefs.get('auto_generate_invoice_number', DEFAULT_PREFS['auto_generate_invoice_number']))
    if auto_gen:
        note_number = claim_next_number(tenant_id, f'{note_type}_note')
    else:
        note_number = str(data.get('note_number') or '').strip()
        if not note_number:
            return jsonify({'error': 'note_number is required when auto-generate is disabled.'}), 400

    now = _now()
    note = {
        'id': str(uuid.uuid4()),
        'tenant_id': tenant_id,
        'note_type': note_type,
        'note_number': note_number,
        'note_date': note_date,
        'status': 'Issued',
        'reason': reason,
        'reason_note': str(data.get('reason_note') or '')[:1000],
        'invoice_id': invoice['id'],
        'invoice_number': invoice.get('invoice_number', ''),
        'invoice_date': invoice.get('issue_date', ''),
        'customer_id': invoice.get('customer_id'),
        'customer_name': invoice.get('customer_name', ''),
        'customer_gstin': str(customer.get('gst_number') or customer.get('gstin') or '').strip().upper(),
        'gst_treatment': invoice.get('gst_treatment') or customer.get('gst_treatment') or 'regular',
        'is_gst_applicable': bool(invoice.get('is_gst_applicable')),
        'place_of_supply': invoice.get('place_of_supply', ''),
        'tax_type': tax_type,
        'items': lines,
        **totals,
        'applied_to_invoice': 0.0,
        'refunded_amount': 0.0,
        'refund_history': [],
        'notes': str(data.get('notes') or '')[:2000],
        'created_by': getattr(request, 'user_id', None),
        'created_at': now,
        'updated_at': now,
    }
    apply_to_invoice = bool(data.get('apply_to_invoice', True))
    before_invoice = copy.deepcopy(invoice)
    updated_invoice, applied = apply_note(invoice, note, apply_to_invoice=apply_to_invoice)
    note['applied_to_invoice'] = applied
    note['unapplied_amount'] = round(totals['total_amount'] - applied, 2) if note_type == 'credit' else 0.0
    updated_invoice['updated_at'] = now

    credit_notes_container.create_item(body=note)
    _save_invoice(updated_invoice)

    label = 'Debit' if note_type == 'debit' else 'Credit'
    log_audit('credit_note', 'create', note['id'], None, note,
              user_id=getattr(request, 'user_id', None), tenant_id=tenant_id,
              entity_label=note_number,
              summary=f"{label} note {note_number} of ₹{totals['total_amount']:,.2f} against "
                      f"invoice {invoice.get('invoice_number', invoice['id'])}")
    log_audit('invoice', 'update', invoice['id'], before_invoice, updated_invoice,
              user_id=getattr(request, 'user_id', None), tenant_id=tenant_id,
              entity_label=invoice.get('invoice_number'),
              summary=f"{label} note {note_number} applied: balance due ₹{updated_invoice['balance_due']:,.2f}")
    dispatch_webhook_event(
        tenant_id=tenant_id, event='credit_note.created',
        payload={'credit_note_id': note['id'], 'note_number': note_number, 'note_type': note_type,
                 'invoice_id': invoice['id'], 'total': totals['total_amount']},
    )
    return jsonify({
        **sanitize_item(note),
        'invoice': {'id': invoice['id'], 'balance_due': updated_invoice['balance_due'],
                    'status': updated_invoice['status']},
        'gst_warnings': note_issues(note, customer, products_by_name) if note['is_gst_applicable'] else [],
    }), 201


# ── List ──────────────────────────────────────────────────────────────────────
@credit_notes_blueprint.route('/credit-notes', methods=['GET'])
@require_permission('invoices', 'view')
def list_credit_notes():
    """List the organisation's notes, newest first. Filters: invoice_id, customer_id, status,
    note_type, q (note or invoice number, customer name); page, page_size."""
    try:
        where = ["c.tenant_id = @tid"]
        params = [{"name": "@tid", "value": request.tenant_id}]
        for arg, field in (('invoice_id', 'invoice_id'), ('customer_id', 'customer_id'),
                           ('status', 'status'), ('note_type', 'note_type')):
            value = (request.args.get(arg) or '').strip()
            if value:
                where.append(f"c.{field} = @{arg}")
                params.append({"name": f"@{arg}", "value": value})
        q = (request.args.get('q') or '').strip().lower()
        if q:
            where.append("(CONTAINS(LOWER(c.note_number), @q) OR CONTAINS(LOWER(c.invoice_number), @q) "
                         "OR CONTAINS(LOWER(c.customer_name), @q))")
            params.append({"name": "@q", "value": q})
        try:
            page = max(1, int(request.args.get('page', 1)))
            page_size = max(1, min(int(request.args.get('page_size', 20)), 100))
        except ValueError:
            page, page_size = 1, 20
        offset = (page - 1) * page_size
        rows = list(credit_notes_container.query_items(
            query=f"SELECT * FROM c WHERE {' AND '.join(where)} ORDER BY c.created_at DESC "
                  f"OFFSET {offset} LIMIT {page_size}",
            parameters=params, enable_cross_partition_query=True,
        ))
        total = list(credit_notes_container.query_items(
            query=f"SELECT VALUE COUNT(1) FROM c WHERE {' AND '.join(where)}",
            parameters=params, enable_cross_partition_query=True,
        ))
        total_count = int(total[0]) if total and isinstance(total[0], (int, float)) else len(rows)
        return jsonify({'items': sanitize_items(rows), 'page': page, 'page_size': page_size,
                        'total': total_count}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@credit_notes_blueprint.route('/credit-notes/next-number', methods=['GET'])
@require_permission('invoices', 'view')
def get_next_note_number():
    """Preview the next credit (or ?note_type=debit) note number without claiming it."""
    note_type = str(request.args.get('note_type') or 'credit').strip().lower()
    if note_type not in NOTE_TYPES:
        return jsonify({'error': "note_type must be 'credit' or 'debit'."}), 400
    try:
        return jsonify({'next_note_number': peek_next_number(request.tenant_id, f'{note_type}_note')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@credit_notes_blueprint.route('/credit-notes/<note_id>', methods=['GET'])
@require_permission('invoices', 'view')
def get_credit_note(note_id):
    note = _load_note(note_id, request.tenant_id)
    if note is None:
        return jsonify({'error': 'Credit note not found'}), 404
    return jsonify(sanitize_item(note)), 200


@credit_notes_blueprint.route('/credit-notes/<note_id>/pdf', methods=['GET'])
@require_permission('invoices', 'view')
def credit_note_pdf(note_id):
    """The note as a PDF, headed CREDIT NOTE / DEBIT NOTE, naming the original invoice and
    the reason (Rule 53(1A))."""
    note = _load_note(note_id, request.tenant_id)
    if note is None:
        return jsonify({'error': 'Credit note not found'}), 404
    label = 'Debit' if note.get('note_type') == 'debit' else 'Credit'
    reason = REASONS.get(note.get('reason'), note.get('reason') or '')
    if note.get('reason_note'):
        reason = f"{reason}: {note['reason_note']}"
    document = {
        **note,
        'invoice_number': note.get('note_number'),
        'issue_date': note.get('note_date'),
        'due_date': note.get('note_date'),
        'notes': (f"{label} note against invoice {note.get('invoice_number')} dated "
                  f"{note.get('invoice_date')}. Reason: {reason}.\n{note.get('notes') or ''}").strip(),
    }
    try:
        mode = get_org_gst_mode(request.tenant_id)
    except Exception:
        mode = 'FULL_GST'
    try:
        pdf = build_invoice_pdf(document, branding=branding_for_document(document, request.tenant_id),
                                doc_type='credit_note' if label == 'Credit' else 'debit_note', gst_mode=mode)
    except Exception as e:
        return jsonify({'error': f'PDF generation failed: {str(e)}'}), 500
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"attachment; filename={label.lower()}_note_{note.get('note_number')}.pdf"
    return response


# ── Cancel (never delete) ─────────────────────────────────────────────────────
@credit_notes_blueprint.route('/credit-notes/<note_id>/cancel', methods=['POST'])
@require_permission('invoices', 'edit')
def cancel_credit_note(note_id):
    """Cancel a note with a reason. Its effect on the invoice is reversed; the note stays on
    record (a cancelled note is still a numbered document)."""
    data = request.get_json(silent=True) or {}
    reason = str(data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'Validation failed', 'details': {'reason': 'Reason is required'}}), 400
    note = _load_note(note_id, request.tenant_id)
    if note is None:
        return jsonify({'error': 'Credit note not found'}), 404
    if not is_live(note):
        return jsonify({'error': 'Credit note is already cancelled'}), 409
    if _num(note.get('refunded_amount')) > 0:
        return jsonify({'error': 'Cannot cancel a note with recorded refunds. Reverse the refunds first.'}), 409

    before_note = copy.deepcopy(note)
    now = _now()
    note.update({'status': 'Cancelled', 'cancel_reason': reason, 'cancelled_at': now,
                 'cancelled_by': getattr(request, 'user_id', None), 'updated_at': now})
    invoice = _load_invoice(note.get('invoice_id'), request.tenant_id)
    if invoice is not None:
        before_invoice = copy.deepcopy(invoice)
        updated_invoice = reverse_note(invoice, note)
        updated_invoice['updated_at'] = now
        _save_invoice(updated_invoice)
        log_audit('invoice', 'update', invoice['id'], before_invoice, updated_invoice,
                  user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id,
                  entity_label=invoice.get('invoice_number'),
                  summary=f"Note {note.get('note_number')} cancelled: balance due ₹{updated_invoice['balance_due']:,.2f}")
    credit_notes_container.replace_item(item=note['id'], body=note)
    log_audit('credit_note', 'cancel', note['id'], before_note, note,
              user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id,
              entity_label=note.get('note_number'),
              summary=f"Note {note.get('note_number')} cancelled: {reason}")
    dispatch_webhook_event(
        tenant_id=request.tenant_id, event='credit_note.cancelled',
        payload={'credit_note_id': note['id'], 'note_number': note.get('note_number'),
                 'invoice_id': note.get('invoice_id')},
    )
    return jsonify(sanitize_item(note)), 200


@credit_notes_blueprint.route('/credit-notes/<note_id>', methods=['DELETE'])
@require_permission('invoices', 'delete')
def delete_credit_note(note_id):
    """Refused on purpose: a note is a numbered tax document. Cancel it instead."""
    return jsonify({'error': 'Credit notes are never deleted. Cancel the note with a reason instead.',
                    'cancel_url': f'/api/credit-notes/{note_id}/cancel'}), 409


# ── Refund of unapplied credit ────────────────────────────────────────────────
@credit_notes_blueprint.route('/credit-notes/<note_id>/refund', methods=['POST'])
@require_permission('invoices', 'edit')
def refund_credit_note(note_id):
    """Record money paid back to the customer out of this note's unapplied credit.
    Body: amount, payment_mode, payment_date, reference?, notes?."""
    data = request.get_json(silent=True) or {}
    errors = {f: f'{f} is required' for f in ('amount', 'payment_mode', 'payment_date') if f not in data}
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400
    try:
        amount = float(data['amount'])
    except (TypeError, ValueError):
        return jsonify({'error': 'Validation failed', 'details': {'amount': 'Must be a number'}}), 400
    if amount <= 0:
        return jsonify({'error': 'Validation failed', 'details': {'amount': 'Must be greater than zero'}}), 400
    if parse_iso_date(data['payment_date']) is None:
        return jsonify({'error': 'Validation failed', 'details': {'payment_date': 'Must be YYYY-MM-DD'}}), 400

    note = _load_note(note_id, request.tenant_id)
    if note is None:
        return jsonify({'error': 'Credit note not found'}), 404
    if not is_live(note):
        return jsonify({'error': 'Cannot refund a cancelled note'}), 409
    if note.get('note_type') != 'credit':
        return jsonify({'error': 'Only a credit note can be refunded'}), 409
    unapplied = round(_num(note.get('total_amount')) - _num(note.get('applied_to_invoice'))
                      - _num(note.get('refunded_amount')), 2)
    if amount > unapplied + 0.005:
        return jsonify({'error': 'Validation failed', 'details': {
            'amount': f'Exceeds the unapplied credit of {unapplied:.2f}'}}), 400

    before = copy.deepcopy(note)
    entry = {
        'id': str(uuid.uuid4()), 'amount': round(amount, 2), 'payment_mode': data['payment_mode'],
        'payment_date': data['payment_date'], 'reference': str(data.get('reference') or ''),
        'notes': str(data.get('notes') or ''), 'recorded_at': _now(),
        'recorded_by': getattr(request, 'user_id', None),
    }
    note.setdefault('refund_history', []).append(entry)
    note['refunded_amount'] = round(_num(note.get('refunded_amount')) + amount, 2)
    note['unapplied_amount'] = round(unapplied - amount, 2)
    note['updated_at'] = _now()
    credit_notes_container.replace_item(item=note['id'], body=note)
    log_audit_event({
        'action': 'REFUND_RECORDED', 'entity': 'credit_note', 'entity_id': note['id'],
        'entity_label': note.get('note_number'), 'before': before, 'after': note,
        'metadata': {'amount': amount, 'payment_mode': data['payment_mode'], 'payment_date': data['payment_date']},
        'user_id': getattr(request, 'user_id', None), 'tenant_id': request.tenant_id,
    })
    return jsonify({'message': 'Refund recorded', 'refund': entry, 'credit_note': sanitize_item(note)}), 200

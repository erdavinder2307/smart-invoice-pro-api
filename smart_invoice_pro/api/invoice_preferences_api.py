"""
Invoice Preferences API
GET  /api/settings/invoice-preferences  – fetch preferences for current tenant (any auth)
PUT  /api/settings/invoice-preferences  – update preferences (Admin only)

Stored as a separate document in the settings container:
    id:  {tenant_id}:invoice_preferences
    partition key: tenant_id

Fields:
    invoice_prefix              – e.g. "INV-"
    invoice_suffix              – e.g. "" or "-2026"
    next_invoice_number         – integer counter, incremented atomically on use
    number_padding              – zero-pad width, e.g. 5 → "00001"
    default_payment_terms       – e.g. "Net 30"
    default_due_days            – integer, days added to issue_date for due_date
    default_notes               – pre-filled Customer Notes on new invoice
    default_terms               – pre-filled Terms & Conditions on new invoice
    auto_generate_invoice_number – bool; when True backend generates & increments
"""

import time
from datetime import datetime

from flask import Blueprint, request, jsonify

from smart_invoice_pro.utils.cosmos_client import settings_container
from smart_invoice_pro.api.roles_api import require_role

invoice_preferences_blueprint = Blueprint('invoice_preferences', __name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PREFS = {
    "invoice_prefix":               "INV-",
    "invoice_suffix":               "",
    "next_invoice_number":          1,
    "number_padding":               5,
    "default_payment_terms":        "Net 30",
    "default_due_days":             30,
    "default_notes":                "Thank you for your business.",
    "default_terms":                "Payment due within 30 days.",
    "auto_generate_invoice_number": True,
}

PAYMENT_TERMS_OPTIONS = [
    "Due on Receipt", "Net 7", "Net 15", "Net 30", "Net 45", "Net 60", "Custom"
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pref_doc_id(tenant_id: str) -> str:
    return f"{tenant_id}:invoice_preferences"


def _get_prefs(tenant_id: str) -> dict:
    """Return the invoice_preferences document for this tenant, or a bare default (unsaved)."""
    doc_id = _pref_doc_id(tenant_id)
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if items:
        return items[0]
    now = datetime.utcnow().isoformat()
    return {
        "id":         doc_id,
        "type":       "invoice_preferences",
        "tenant_id":  tenant_id,
        **DEFAULT_PREFS,
        "created_at": now,
        "updated_at": now,
    }


def _safe_prefs(doc: dict) -> dict:
    """Strip CosmosDB internal fields before returning to client."""
    return {k: v for k, v in doc.items() if not k.startswith('_')}


def format_invoice_number(prefix: str, number: int, padding: int, suffix: str) -> str:
    padding = max(1, min(10, int(padding)))
    return f"{prefix}{str(int(number)).zfill(padding)}{suffix}"


def generate_invoice_number(tenant_id: str) -> str:
    """
    Atomically claim the next invoice number for this tenant.

    Uses optimistic concurrency (ETag) with exponential-backoff retries so that
    concurrent invoice creation requests never produce duplicate numbers.

    Returns the formatted invoice number string (e.g. "INV-00042").
    """
    try:
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError
    except ImportError:
        CosmosAccessConditionFailedError = Exception  # fallback; shouldn't happen

    max_retries = 8

    for attempt in range(max_retries):
        prefs = _get_prefs(tenant_id)
        old_next = max(1, int(prefs.get('next_invoice_number', 1)))
        etag = prefs.get('_etag')

        updated = {
            **prefs,
            'next_invoice_number': old_next + 1,
            'updated_at': datetime.utcnow().isoformat(),
        }
        # Remove internal Cosmos fields so replace_item doesn't reject them
        updated.pop('_etag', None)
        updated.pop('_ts', None)
        updated.pop('_rid', None)
        updated.pop('_self', None)
        updated.pop('_attachments', None)

        try:
            if etag:
                settings_container.replace_item(
                    item=updated['id'],
                    body=updated,
                    if_match_etag=etag,
                )
            else:
                # First time — create; if concurrent, one will get a 409 and retry
                settings_container.create_item(body=updated)

            # Claimed old_next successfully
            prefix  = prefs.get('invoice_prefix', DEFAULT_PREFS['invoice_prefix'])
            suffix  = prefs.get('invoice_suffix', DEFAULT_PREFS['invoice_suffix'])
            padding = prefs.get('number_padding', DEFAULT_PREFS['number_padding'])
            return format_invoice_number(prefix, old_next, padding, suffix)

        except CosmosAccessConditionFailedError:
            # ETag mismatch — another request updated the counter first; retry
            if attempt < max_retries - 1:
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise RuntimeError("Could not acquire invoice number: too many concurrent requests")

        except Exception as e:
            # Includes 409 Conflict on concurrent creates
            if '409' in str(e) or 'Conflict' in str(e):
                if attempt < max_retries - 1:
                    time.sleep(0.05 * (2 ** attempt))
                    continue
            raise

    raise RuntimeError("Could not acquire invoice number after retries")


def peek_next_invoice_number(tenant_id: str) -> str:
    """
    Return what the next invoice number WOULD BE without incrementing.
    Used by the frontend for display / preview only.
    """
    prefs = _get_prefs(tenant_id)
    prefix  = prefs.get('invoice_prefix', DEFAULT_PREFS['invoice_prefix'])
    suffix  = prefs.get('invoice_suffix', DEFAULT_PREFS['invoice_suffix'])
    padding = prefs.get('number_padding', DEFAULT_PREFS['number_padding'])
    next_n  = max(1, int(prefs.get('next_invoice_number', 1)))
    return format_invoice_number(prefix, next_n, padding, suffix)


# ── GET /api/settings/invoice-preferences ────────────────────────────────────
@invoice_preferences_blueprint.route('/settings/invoice-preferences', methods=['GET'])
def get_invoice_preferences():
    """Return invoice preferences for the current tenant (any authenticated user)."""
    try:
        prefs = _get_prefs(request.tenant_id)
        return jsonify(_safe_prefs(prefs)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PUT /api/settings/invoice-preferences ───────────────────────────────────
@invoice_preferences_blueprint.route('/settings/invoice-preferences', methods=['PUT'])
@require_role('Admin')
def update_invoice_preferences():
    """Update invoice preferences (Admin only)."""
    try:
        data = request.get_json() or {}

        # ── Validation ────────────────────────────────────────────────────────
        errors = {}

        prefix = data.get('invoice_prefix', DEFAULT_PREFS['invoice_prefix'])
        if not isinstance(prefix, str) or len(prefix.strip()) == 0:
            errors['invoice_prefix'] = 'Prefix cannot be empty.'
        elif len(prefix) > 20:
            errors['invoice_prefix'] = 'Prefix must be 20 characters or fewer.'

        suffix = data.get('invoice_suffix', DEFAULT_PREFS['invoice_suffix'])
        if not isinstance(suffix, str):
            errors['invoice_suffix'] = 'Suffix must be a string.'
        elif len(suffix) > 20:
            errors['invoice_suffix'] = 'Suffix must be 20 characters or fewer.'

        next_num = data.get('next_invoice_number', DEFAULT_PREFS['next_invoice_number'])
        try:
            next_num = int(next_num)
            if next_num < 1:
                errors['next_invoice_number'] = 'Next invoice number must be >= 1.'
        except (TypeError, ValueError):
            errors['next_invoice_number'] = 'Next invoice number must be a positive integer.'

        padding = data.get('number_padding', DEFAULT_PREFS['number_padding'])
        try:
            padding = int(padding)
            if not 1 <= padding <= 10:
                errors['number_padding'] = 'Padding must be between 1 and 10.'
        except (TypeError, ValueError):
            errors['number_padding'] = 'Padding must be an integer between 1 and 10.'

        due_days = data.get('default_due_days', DEFAULT_PREFS['default_due_days'])
        try:
            due_days = int(due_days)
            if due_days < 0:
                errors['default_due_days'] = 'Due days must be >= 0.'
        except (TypeError, ValueError):
            errors['default_due_days'] = 'Due days must be a non-negative integer.'

        default_notes = data.get('default_notes', DEFAULT_PREFS['default_notes'])
        if not isinstance(default_notes, str):
            errors['default_notes'] = 'Notes must be a string.'
        elif len(default_notes) > 2000:
            errors['default_notes'] = 'Notes must be 2000 characters or fewer.'

        default_terms = data.get('default_terms', DEFAULT_PREFS['default_terms'])
        if not isinstance(default_terms, str):
            errors['default_terms'] = 'Terms must be a string.'
        elif len(default_terms) > 2000:
            errors['default_terms'] = 'Terms must be 2000 characters or fewer.'

        if errors:
            return jsonify({'error': 'Validation failed', 'fields': errors}), 400

        # ── Merge onto existing ───────────────────────────────────────────────
        existing = _get_prefs(request.tenant_id)
        now = datetime.utcnow().isoformat()

        existing['invoice_prefix']               = prefix.strip()
        existing['invoice_suffix']               = suffix
        existing['next_invoice_number']          = next_num
        existing['number_padding']               = padding
        existing['default_payment_terms']        = str(data.get('default_payment_terms', existing.get('default_payment_terms', DEFAULT_PREFS['default_payment_terms'])))[:100]
        existing['default_due_days']             = due_days
        existing['default_notes']               = default_notes
        existing['default_terms']               = default_terms
        existing['auto_generate_invoice_number'] = bool(data.get('auto_generate_invoice_number', existing.get('auto_generate_invoice_number', True)))
        existing['updated_at']                  = now
        if 'created_at' not in existing:
            existing['created_at'] = now

        settings_container.upsert_item(existing)
        return jsonify(_safe_prefs(existing)), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

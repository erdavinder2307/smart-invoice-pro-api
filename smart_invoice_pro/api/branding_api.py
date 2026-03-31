"""
Branding Settings API
GET  /api/settings/branding  – fetch branding for current tenant (any auth)
PUT  /api/settings/branding  – update branding (Admin only)

Branding is stored as extra fields on the existing organization_profile document
(same document ID: {tenant_id}:organization_profile, same settings container).

Fields managed here:
    primary_color              – hex, e.g. "#2563EB"  (UI primary colour)
    secondary_color            – hex, e.g. "#10B981"  (secondary / success)
    accent_color               – hex, e.g. "#2d6cdf"  (invoice / PDF accent)
    email_header_logo_url      – URL for the image shown in email headers
    invoice_template_settings:
        show_logo              – bool
        show_signature         – bool
"""

import re
from datetime import datetime

from flask import Blueprint, request, jsonify

from smart_invoice_pro.utils.cosmos_client import settings_container
from smart_invoice_pro.api.roles_api import require_role
from smart_invoice_pro.api.organization_profile_api import _get_profile, _safe

branding_blueprint = Blueprint('branding', __name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_BRANDING = {
    "primary_color":         "#2563EB",
    "secondary_color":       "#10B981",
    "accent_color":          "#2d6cdf",
    "email_header_logo_url": "",
    "invoice_template_settings": {
        "show_logo":      True,
        "show_signature": False,
    },
}

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _resolve(value, key):
    """Return value if non-empty, else the hardcoded default for that key."""
    return (value or '').strip() or DEFAULT_BRANDING[key]


def _extract_branding(profile: dict) -> dict:
    """Project only branding fields out of a full org-profile document."""
    its = profile.get('invoice_template_settings') or {}
    return {
        "primary_color":         _resolve(profile.get('primary_color'),   'primary_color'),
        "secondary_color":       _resolve(profile.get('secondary_color'), 'secondary_color'),
        "accent_color":          _resolve(profile.get('accent_color'),    'accent_color'),
        "logo_url":              (profile.get('logo_url') or ''),
        "email_header_logo_url": (profile.get('email_header_logo_url') or ''),
        "invoice_template_settings": {
            "show_logo":      bool(its.get('show_logo',      True)),
            "show_signature": bool(its.get('show_signature', False)),
        },
    }


# ── GET /api/settings/branding ────────────────────────────────────────────────
@branding_blueprint.route('/settings/branding', methods=['GET'])
def get_branding():
    """Return branding settings for the current tenant."""
    try:
        profile = _get_profile(request.tenant_id)
        return jsonify(_extract_branding(profile)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PUT /api/settings/branding ────────────────────────────────────────────────
@branding_blueprint.route('/settings/branding', methods=['PUT'])
@require_role('Admin')
def update_branding():
    """Update branding settings for the current tenant (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}

        # Validate optional hex color fields
        for field in ('primary_color', 'secondary_color', 'accent_color'):
            val = (data.get(field) or '').strip()
            if val and not _HEX_RE.match(val):
                return jsonify({
                    'error': f"{field} must be a valid 6-digit hex colour (e.g. #2563EB)"
                }), 400

        existing = _get_profile(request.tenant_id)
        its_in   = data.get('invoice_template_settings') or {}
        ext_its  = existing.get('invoice_template_settings') or {}

        # Merge branding fields into the existing profile document
        for field in ('primary_color', 'secondary_color', 'accent_color'):
            new_val = (data.get(field) or '').strip()
            if new_val:
                existing[field] = new_val
            elif field not in existing:
                existing[field] = DEFAULT_BRANDING[field]

        if 'email_header_logo_url' in data:
            existing['email_header_logo_url'] = (data['email_header_logo_url'] or '').strip()

        existing['invoice_template_settings'] = {
            'show_logo': (
                its_in['show_logo'] if 'show_logo' in its_in
                else ext_its.get('show_logo', True)
            ),
            'show_signature': (
                its_in['show_signature'] if 'show_signature' in its_in
                else ext_its.get('show_signature', False)
            ),
        }

        existing['updated_at'] = datetime.utcnow().isoformat()

        # Ensure the document has an id so upsert doesn't create a duplicate
        if 'id' not in existing:
            from smart_invoice_pro.api.organization_profile_api import _doc_id
            existing['id'] = _doc_id(request.tenant_id)
        if 'tenant_id' not in existing:
            existing['tenant_id'] = request.tenant_id
        if 'type' not in existing:
            existing['type'] = 'organization_profile'

        settings_container.upsert_item(existing)
        return jsonify(_extract_branding(existing)), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

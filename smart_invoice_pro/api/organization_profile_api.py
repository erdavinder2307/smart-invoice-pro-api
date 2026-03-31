"""
Organization Profile Settings API
GET  /api/settings/organization-profile  - fetch org profile for current tenant
PUT  /api/settings/organization-profile  - update org profile (Admin only)
POST /api/settings/upload-logo           - upload org logo (Admin only)

Document stored in `settings` container:
{
    "id":                "{tenant_id}:organization_profile",
    "type":              "organization_profile",
    "tenant_id":         "<tenant_id>",
    "organization_name": "Acme Corp",
    "industry":          "Technology",
    "country":           "India",
    "gstin":             "22AAAAA0000A1Z5",
    "website_url":       "https://acme.com",
    "logo_url":          "/uploads/org_logos/<filename>",
    "address": {
        "line1":  "123 Main Street",
        "line2":  "Floor 2",
        "city":   "Mumbai",
        "state":  "Maharashtra",
        "pincode": "400001",
        "phone":  "+91-9876543210",
        "fax":    ""
    },
    "created_at":  "...",
    "updated_at":  "..."
}
"""

import os
import uuid
import base64
from datetime import datetime

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from smart_invoice_pro.utils.cosmos_client import settings_container
from smart_invoice_pro.api.roles_api import require_role

org_profile_blueprint = Blueprint('org_profile', __name__)

ORG_LOGO_UPLOAD_FOLDER = 'uploads/org_logos'
ALLOWED_LOGO_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_LOGO_BYTES = 1 * 1024 * 1024  # 1 MB

os.makedirs(ORG_LOGO_UPLOAD_FOLDER, exist_ok=True)


def _doc_id(tenant_id: str) -> str:
    return f"{tenant_id}:organization_profile"


def _get_profile(tenant_id: str) -> dict:
    """Return the org profile document for this tenant, or a bare default."""
    doc_id = _doc_id(tenant_id)
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
    # Return an unsaved default
    return {
        "id":                doc_id,
        "type":              "organization_profile",
        "tenant_id":         tenant_id,
        "organization_name": "",
        "industry":          "",
        "country":           "",
        "gstin":             "",
        "website_url":       "",
        "logo_url":          "",
        "address": {
            "line1":   "",
            "line2":   "",
            "city":    "",
            "state":   "",
            "pincode": "",
            "phone":   "",
            "fax":     "",
        },
        "created_at":  None,
        "updated_at":  None,
    }


def _safe(doc: dict) -> dict:
    """Strip CosmosDB internal fields from response."""
    return {k: v for k, v in doc.items() if not k.startswith('_')}


# ── GET /api/settings/organization-profile ────────────────────────────────────
@org_profile_blueprint.route('/settings/organization-profile', methods=['GET'])
def get_org_profile():
    """Fetch the organization profile for the current tenant."""
    try:
        profile = _get_profile(request.tenant_id)
        return jsonify(_safe(profile)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PUT /api/settings/organization-profile ────────────────────────────────────
@org_profile_blueprint.route('/settings/organization-profile', methods=['PUT'])
@require_role('Admin')
def update_org_profile():
    """Create or update the organization profile for the current tenant (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}

        # Required field validation
        organization_name = (data.get('organization_name') or '').strip()
        country = (data.get('country') or '').strip()
        if not organization_name:
            return jsonify({'error': 'organization_name is required'}), 400
        if not country:
            return jsonify({'error': 'country is required'}), 400

        now = datetime.utcnow().isoformat()
        existing = _get_profile(request.tenant_id)
        created_at = existing.get('created_at') or now

        address_in = data.get('address') or {}
        address = {
            "line1":   (address_in.get('line1')   or '').strip(),
            "line2":   (address_in.get('line2')   or '').strip(),
            "city":    (address_in.get('city')    or '').strip(),
            "state":   (address_in.get('state')   or '').strip(),
            "pincode": (address_in.get('pincode') or '').strip(),
            "phone":   (address_in.get('phone')   or '').strip(),
            "fax":     (address_in.get('fax')     or '').strip(),
        }

        doc = {
            "id":                _doc_id(request.tenant_id),
            "type":              "organization_profile",
            "tenant_id":         request.tenant_id,
            "organization_name": organization_name,
            "industry":          (data.get('industry')    or '').strip(),
            "country":           country,
            "gstin":             (data.get('gstin')        or '').strip(),
            "website_url":       (data.get('website_url') or '').strip(),
            "logo_url":          (data.get('logo_url')    or existing.get('logo_url', '')).strip(),
            "address":           address,
            # ── GST settings ─────────────────────────────────────────────────
            "gst_enabled":       bool(data.get('gst_enabled', existing.get('gst_enabled', True))),
            "gst_registration_type": (data.get('gst_registration_type') or existing.get('gst_registration_type', 'regular')).strip(),
            # ── Preserve branding fields (managed by branding_api) ──────────
            "primary_color":              existing.get('primary_color',   ''),
            "secondary_color":            existing.get('secondary_color', ''),
            "accent_color":               existing.get('accent_color',    ''),
            "email_header_logo_url":      existing.get('email_header_logo_url', ''),
            "invoice_template_settings":  existing.get('invoice_template_settings', {}),
            "created_at":        created_at,
            "updated_at":        now,
        }

        settings_container.upsert_item(doc)
        return jsonify(_safe(doc)), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── GET /api/settings/gst-config ────────────────────────────────────────────
@org_profile_blueprint.route('/settings/gst-config', methods=['GET'])
def get_gst_config():
    """Return tenant GST configuration needed by the tax calculation engine."""
    try:
        from smart_invoice_pro.api.gst_api import extract_state_from_gstin, validate_gstin_format
        profile = _get_profile(request.tenant_id)
        gstin = profile.get('gstin', '')
        # Derive seller state: from GSTIN first, then address.state
        seller_state = ''
        if gstin and validate_gstin_format(gstin):
            seller_state = extract_state_from_gstin(gstin)
        if not seller_state:
            seller_state = (profile.get('address') or {}).get('state', '')
        return jsonify({
            'gst_enabled':            profile.get('gst_enabled', True),
            'gstin':                   gstin,
            'seller_state':           seller_state,
            'gst_registration_type':  profile.get('gst_registration_type', 'regular'),
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── POST /api/settings/upload-logo ───────────────────────────────────────────
@org_profile_blueprint.route('/settings/upload-logo', methods=['POST'])
@require_role('Admin')
def upload_org_logo():
    """Upload the organization logo. Accepts { logo_filename, logo_base64 } (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}

        logo_filename = (data.get('logo_filename') or '').strip()
        logo_base64 = (data.get('logo_base64') or '').strip()

        if not logo_filename or not logo_base64:
            return jsonify({'error': 'logo_filename and logo_base64 are required'}), 400

        # Validate file extension
        ext = logo_filename.rsplit('.', 1)[-1].lower() if '.' in logo_filename else ''
        if ext not in ALLOWED_LOGO_EXTENSIONS:
            return jsonify({'error': f'File type not allowed. Allowed types: {", ".join(sorted(ALLOWED_LOGO_EXTENSIONS))}'}), 400

        # Decode base64 (strip data URI prefix if present)
        raw = logo_base64.split(',')[1] if ',' in logo_base64 else logo_base64
        try:
            file_bytes = base64.b64decode(raw)
        except Exception:
            return jsonify({'error': 'Invalid base64 encoding'}), 400

        # Validate size
        if len(file_bytes) > MAX_LOGO_BYTES:
            return jsonify({'error': 'Logo file must be smaller than 1 MB'}), 400

        # Save file safely
        safe_name = secure_filename(f"{request.tenant_id}_{uuid.uuid4().hex}_{logo_filename}")
        file_path = os.path.join(ORG_LOGO_UPLOAD_FOLDER, safe_name)
        with open(file_path, 'wb') as f:
            f.write(file_bytes)

        logo_url = f"/uploads/org_logos/{safe_name}"
        return jsonify({'logo_url': logo_url}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

"""
Integrations Settings API
GET /api/settings/integrations  — fetch integration config for the current tenant
PUT /api/settings/integrations  — save integration config

Schema stored in `settings` container:
{
    "id":         "<tenant_id>:integrations_settings",
    "type":       "integrations_settings",
    "tenant_id":  "<tenant_id>",

    "payments": {
        "provider":        "zoho",
        "enabled":         false,
        "api_key":         "<stored-encrypted>",
        "webhook_secret":  "<stored-encrypted>",
        "status":          "disconnected"
    },
    "banking": {
        "enabled":  false,
        "provider": null
    },
    "email": {
        "provider":      "azure",
        "sender_email":  "<from env: AZURE_SENDER_EMAIL>",
        "enabled":       true
    },
    "webhooks": [
        {
            "id":     "<uuid>",
            "url":    "https://...",
            "events": ["invoice.created", "payment.received"],
            "active": true
        }
    ],
    "created_at": "...",
    "updated_at": "..."
}

Security note:
  - API keys and webhook secrets are stored as-is in Cosmos DB (which encrypts
    at rest).  They are NEVER returned to the frontend in plain text — the GET
    endpoint replaces them with masked placeholders (e.g. "••••••••••ab1c").
  - To update a secret the client must send the full new value explicitly.
    Sending the masked placeholder value is detected and the field is left
    unchanged.
"""
import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import settings_container

integrations_blueprint = Blueprint('integrations', __name__)

# ── Supported webhook events ───────────────────────────────────────────────────
SUPPORTED_EVENTS = {
    "invoice.created",
    "invoice.paid",
    "customer.created",
}

# ── Masks ──────────────────────────────────────────────────────────────────────
_MASK = "••••••••••"

def _mask_secret(value: str | None) -> str | None:
    """Return a masked representation — show last 4 chars only."""
    if not value:
        return None
    if len(value) <= 4:
        return _MASK
    return _MASK + value[-4:]


def _is_masked(value: str | None) -> bool:
    """Return True if the value looks like our masked placeholder (unchanged)."""
    if not value:
        return False
    return value.startswith(_MASK)


# ── Defaults ───────────────────────────────────────────────────────────────────
def _default_doc(tenant_id: str) -> dict:
    return {
        "id":        f"{tenant_id}:integrations_settings",
        "type":      "integrations_settings",
        "tenant_id": tenant_id,
        "payments": {
            "provider":       "zoho",
            "enabled":        False,
            "api_key":        None,
            "webhook_secret": None,
            "status":         "disconnected",
        },
        "banking": {
            "enabled":  False,
            "provider": None,
        },
        "email": {
            "provider":     "azure",
            "sender_email": os.getenv("AZURE_SENDER_EMAIL", ""),
            "enabled":      True,
        },
        "webhooks": [],
        "created_at": datetime.utcnow().isoformat(),
    }


def _get_doc(tenant_id: str) -> dict:
    doc_id = f"{tenant_id}:integrations_settings"
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
    return _default_doc(tenant_id)


def _safe_doc(doc: dict) -> dict:
    """Return a copy of doc safe for sending to the frontend (secrets masked)."""
    import copy
    safe = copy.deepcopy(doc)
    # Strip Cosmos internal fields
    for key in list(safe.keys()):
        if key.startswith("_"):
            del safe[key]

    # Mask payment secrets
    if isinstance(safe.get("payments"), dict):
        safe["payments"]["api_key"] = _mask_secret(safe["payments"].get("api_key"))
        safe["payments"]["webhook_secret"] = _mask_secret(safe["payments"].get("webhook_secret"))

    return safe


# ── GET ────────────────────────────────────────────────────────────────────────
@integrations_blueprint.route('/settings/integrations', methods=['GET'])
def get_integrations_settings():
    """Fetch integration settings for the current tenant (secrets masked)."""
    try:
        doc  = _get_doc(request.tenant_id)
        return jsonify(_safe_doc(doc)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── PUT ────────────────────────────────────────────────────────────────────────
@integrations_blueprint.route('/settings/integrations', methods=['PUT'])
def save_integrations_settings():
    """Save integration settings for the current tenant."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data provided."}), 400

    try:
        doc = _get_doc(request.tenant_id)

        # ── Payments ──────────────────────────────────────────────────────────
        if "payments" in data:
            p_in  = data["payments"]
            p_doc = doc.setdefault("payments", {
                "provider": "zoho", "enabled": False,
                "api_key": None, "webhook_secret": None, "status": "disconnected"
            })

            if "enabled" in p_in:
                p_doc["enabled"] = bool(p_in["enabled"])

            if "provider" in p_in:
                p_doc["provider"] = str(p_in["provider"])

            # Only update secrets when the client sends a real (non-masked) value
            if "api_key" in p_in and not _is_masked(p_in["api_key"]):
                p_doc["api_key"] = p_in["api_key"] or None

            if "webhook_secret" in p_in and not _is_masked(p_in["webhook_secret"]):
                p_doc["webhook_secret"] = p_in["webhook_secret"] or None

            # Derive status from whether we have credentials + enabled
            has_creds = bool(p_doc.get("api_key"))
            if p_doc["enabled"] and has_creds:
                p_doc["status"] = "connected"
            elif p_doc["enabled"] and not has_creds:
                p_doc["status"] = "pending"
            else:
                p_doc["status"] = "disconnected"

        # ── Banking ───────────────────────────────────────────────────────────
        if "banking" in data:
            b_in  = data["banking"]
            b_doc = doc.setdefault("banking", {"enabled": False, "provider": None})

            if "enabled" in b_in:
                b_doc["enabled"] = bool(b_in["enabled"])
            if "provider" in b_in:
                b_doc["provider"] = b_in["provider"] or None

        # ── Email ─────────────────────────────────────────────────────────────
        if "email" in data:
            e_in  = data["email"]
            e_doc = doc.setdefault("email", {
                "provider": "azure", "sender_email": "", "enabled": True
            })

            if "enabled" in e_in:
                e_doc["enabled"] = bool(e_in["enabled"])
            # sender_email is admin-configurable but validated as a string
            if "sender_email" in e_in:
                val = str(e_in["sender_email"]).strip()
                e_doc["sender_email"] = val

        # ── Webhooks ──────────────────────────────────────────────────────────
        if "webhooks" in data:
            incoming = data["webhooks"]
            if not isinstance(incoming, list):
                return jsonify({"error": "'webhooks' must be an array."}), 400

            validated = []
            for wh in incoming:
                url = str(wh.get("url", "")).strip()
                if not url.startswith(("https://", "http://")):
                    return jsonify({"error": f"Invalid webhook URL: {url!r}"}), 400

                events = wh.get("events", [])
                if not isinstance(events, list):
                    return jsonify({"error": "Webhook 'events' must be an array."}), 400
                for ev in events:
                    if ev not in SUPPORTED_EVENTS:
                        return jsonify({
                            "error": f"Unsupported event: {ev!r}. Supported: {sorted(SUPPORTED_EVENTS)}"
                        }), 400

                validated.append({
                    "id":     wh.get("id") or str(uuid.uuid4()),
                    "url":    url,
                    "events": events,
                    "active": bool(wh.get("active", True)),
                })
            doc["webhooks"] = validated

        doc["updated_at"] = datetime.utcnow().isoformat()
        settings_container.upsert_item(body=doc)

        return jsonify({
            "message":  "Integration settings saved.",
            "settings": _safe_doc(doc),
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

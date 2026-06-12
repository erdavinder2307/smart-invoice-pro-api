"""
Integrations Settings API
GET  /api/settings/integrations               — fetch integration config for the current tenant
PUT  /api/settings/integrations               — save integration config
POST /api/settings/integrations/test-email    — send a test email via Azure ACS
GET  /api/settings/integrations/webhook-logs  — recent webhook delivery log (last 50)

Schema stored in `settings` container:
{
    "id":         "<tenant_id>:integrations_settings",
    "type":       "integrations_settings",
    "tenant_id":  "<tenant_id>",

    "email": {
        "provider":     "azure",
        "sender_email": "<from env: AZURE_SENDER_EMAIL>",
        "sender_name":  "Solidev Books",
        "enabled":      true
    },
    "webhooks": [
        {
            "id":     "<uuid>",
            "url":    "https://...",
            "events": ["invoice.created"],
            "active": true,
            "secret": "<per-endpoint HMAC secret — stored masked>"
        }
    ],
    "created_at": "...",
    "updated_at": "..."
}

Security notes:
  - Per-webhook secrets are stored as-is in Cosmos DB (which encrypts at rest).
    They are NEVER returned to the frontend in plain text — the GET endpoint
    replaces them with masked placeholders (e.g. "••••••••••ab1c").
  - To update a secret the client must send the full new value explicitly.
    Sending the masked placeholder value is detected and the field is left
    unchanged.
  - All webhook URLs must use https://.
"""
import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import settings_container, webhook_logs_container

integrations_blueprint = Blueprint('integrations', __name__)

# ── Supported webhook events ───────────────────────────────────────────────────
SUPPORTED_EVENTS = {
    # Invoice lifecycle
    "invoice.created",
    "invoice.updated",
    "invoice.paid",
    "invoice.voided",
    # Quote lifecycle
    "quote.created",
    "quote.accepted",
    "quote.converted",
    # Customer
    "customer.created",
    "customer.updated",
    # Banking
    "bank_import.completed",
    "reconciliation.completed",
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
        "email": {
            "provider":     "azure",
            "sender_email": os.getenv("AZURE_SENDER_EMAIL", ""),
            "sender_name":  "Solidev Books",
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

    # Mask per-webhook secrets
    for wh in safe.get("webhooks", []):
        if wh.get("secret"):
            wh["secret"] = _mask_secret(wh["secret"])

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

        # ── Email ─────────────────────────────────────────────────────────────
        if "email" in data:
            e_in  = data["email"]
            e_doc = doc.setdefault("email", {
                "provider": "azure", "sender_email": "",
                "sender_name": "Solidev Books", "enabled": True,
            })

            if "enabled" in e_in:
                e_doc["enabled"] = bool(e_in["enabled"])
            if "sender_email" in e_in:
                e_doc["sender_email"] = str(e_in["sender_email"]).strip()
            if "sender_name" in e_in:
                e_doc["sender_name"] = str(e_in["sender_name"]).strip()

        # ── Webhooks ──────────────────────────────────────────────────────────
        if "webhooks" in data:
            incoming = data["webhooks"]
            if not isinstance(incoming, list):
                return jsonify({"error": "'webhooks' must be an array."}), 400

            validated = []
            for wh in incoming:
                url = str(wh.get("url", "")).strip()
                if not url.startswith("https://"):
                    return jsonify({"error": f"Webhook URL must use https://. Got: {url!r}"}), 400

                events = wh.get("events", [])
                if not isinstance(events, list):
                    return jsonify({"error": "Webhook 'events' must be an array."}), 400
                for ev in events:
                    if ev not in SUPPORTED_EVENTS:
                        return jsonify({
                            "error": f"Unsupported event: {ev!r}. Supported: {sorted(SUPPORTED_EVENTS)}"
                        }), 400

                # Resolve per-endpoint secret — keep existing if client sends masked value
                wh_id = wh.get("id") or str(uuid.uuid4())
                existing_secret = None
                for existing_wh in doc.get("webhooks", []):
                    if existing_wh.get("id") == wh_id:
                        existing_secret = existing_wh.get("secret")
                        break
                new_secret_raw = wh.get("secret")
                if not new_secret_raw or _is_masked(new_secret_raw):
                    resolved_secret = existing_secret
                else:
                    resolved_secret = new_secret_raw

                validated.append({
                    "id":     wh_id,
                    "url":    url,
                    "events": events,
                    "active": bool(wh.get("active", True)),
                    "secret": resolved_secret,
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


# ── POST /settings/integrations/test-email ────────────────────────────────────
@integrations_blueprint.route('/settings/integrations/test-email', methods=['POST'])
def test_email_connection():
    """Send a test email to the authenticated user's address via Azure ACS."""
    try:
        from azure.communication.email import EmailClient

        # Validate recipient first — before any infrastructure checks
        body = request.get_json(silent=True) or {}
        recipient = str(body.get("to") or "").strip()
        if not recipient or "@" not in recipient:
            return jsonify({"error": "Provide a valid 'to' email address."}), 400

        connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
        if not connection_string:
            return jsonify({"error": "AZURE_EMAIL_CONNECTION_STRING is not configured."}), 503

        doc = _get_doc(request.tenant_id)
        email_cfg = doc.get("email", {})
        sender = email_cfg.get("sender_email") or os.getenv("AZURE_SENDER_EMAIL", "")
        sender_name = email_cfg.get("sender_name") or "Solidev Books"

        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send({
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"Test Email from {sender_name} (Solidev Books)",
                "html": (
                    "<p>This is a test email sent from <strong>Solidev Books</strong>.</p>"
                    "<p>If you received this, your email integration is working correctly.</p>"
                ),
            },
        })
        poller.result()  # wait for delivery confirmation
        return jsonify({"message": "Test email sent successfully.", "recipient": recipient}), 200

    except Exception as e:
        return jsonify({"error": f"Test email failed: {str(e)}"}), 500


# ── GET /settings/integrations/webhook-logs ───────────────────────────────────
@integrations_blueprint.route('/settings/integrations/webhook-logs', methods=['GET'])
def get_webhook_logs():
    """Return the 50 most recent webhook delivery log entries for this tenant."""
    try:
        from smart_invoice_pro.utils.cosmos_client import webhook_logs_container
        items = list(webhook_logs_container.query_items(
            query=(
                "SELECT TOP 50 * FROM c WHERE c.tenant_id = @tid "
                "ORDER BY c.delivered_at DESC"
            ),
            parameters=[{"name": "@tid", "value": request.tenant_id}],
            enable_cross_partition_query=True,
        ))
        # Strip Cosmos internal fields
        safe = [{k: v for k, v in item.items() if not k.startswith("_")}
                for item in items]
        return jsonify(safe), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

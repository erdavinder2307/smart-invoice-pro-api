"""
Automation Settings API
GET /api/settings/automation  — fetch automation config for the current tenant
PUT /api/settings/automation  — save automation config

Schema stored in `settings` container under id = "<tenant_id>:automation_settings":

{
    "id":                  "<tenant_id>:automation_settings",
    "type":                "automation_settings",
    "tenant_id":           "<tenant_id>",
    "email_enabled":       true,
    "payment_reminders": [
        { "type": "before_due", "days": 3, "enabled": true },
        { "type": "on_due",     "days": 0, "enabled": true },
        { "type": "after_due",  "days": 2, "enabled": true }
    ],
    "created_at":          "...",
    "updated_at":          "..."
}
"""
from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import settings_container
from datetime import datetime

automation_blueprint = Blueprint('automation', __name__)

VALID_TYPES = {'before_due', 'on_due', 'after_due'}

DEFAULT_REMINDERS = [
    {"type": "before_due", "days": 3, "enabled": True},
    {"type": "on_due",     "days": 0, "enabled": True},
    {"type": "after_due",  "days": 2, "enabled": True},
]

DEFAULT_CONFIG = {
    "email_enabled":      True,
    "payment_reminders":  DEFAULT_REMINDERS,
}


def _get_doc(tenant_id: str) -> dict:
    doc_id = f"{tenant_id}:automation_settings"
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if items:
        return items[0]
    return {
        **DEFAULT_CONFIG,
        "id":         doc_id,
        "type":       "automation_settings",
        "tenant_id":  tenant_id,
        "created_at": datetime.utcnow().isoformat(),
    }


def _validate_reminders(reminders: list) -> tuple[list | None, str | None]:
    """Validate and normalise incoming payment_reminders array.
    Returns (validated_list, error_message).
    """
    seen_types: dict[str, int] = {}
    validated = []
    for r in reminders:
        rtype = r.get("type")
        if rtype not in VALID_TYPES:
            return None, f"Invalid reminder type: {rtype!r}. Must be one of {sorted(VALID_TYPES)}."
        days = int(r.get("days", 0))
        if rtype == "on_due":
            days = 0
        elif not (1 <= days <= 60):
            return None, f"'days' must be 1–60 for type '{rtype}' (got {days})."
        # Enforce at most one rule per type
        if rtype in seen_types:
            return None, f"Duplicate reminder type: {rtype!r}. Each type may appear at most once."
        seen_types[rtype] = days
        validated.append({
            "type":    rtype,
            "days":    days,
            "enabled": bool(r.get("enabled", True)),
        })
    return validated, None


# ── GET ────────────────────────────────────────────────────────────────────────
@automation_blueprint.route('/settings/automation', methods=['GET'])
def get_automation_settings():
    """Fetch automation / reminder settings for the current tenant."""
    try:
        doc  = _get_doc(request.tenant_id)
        safe = {k: v for k, v in doc.items() if not k.startswith('_')}
        return jsonify(safe), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PUT ────────────────────────────────────────────────────────────────────────
@automation_blueprint.route('/settings/automation', methods=['PUT'])
def save_automation_settings():
    """Save automation / reminder settings for the current tenant."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided.'}), 400

    try:
        doc = _get_doc(request.tenant_id)

        if 'email_enabled' in data:
            doc['email_enabled'] = bool(data['email_enabled'])

        if 'payment_reminders' in data:
            validated, err = _validate_reminders(data['payment_reminders'])
            if err:
                return jsonify({'error': err}), 400
            doc['payment_reminders'] = validated

        doc['updated_at'] = datetime.utcnow().isoformat()
        settings_container.upsert_item(body=doc)

        safe = {k: v for k, v in doc.items() if not k.startswith('_')}
        return jsonify({'message': 'Automation settings saved.', 'settings': safe}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

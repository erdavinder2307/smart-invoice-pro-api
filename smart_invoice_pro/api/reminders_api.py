"""
Payment Reminders Settings API
GET  /api/settings/reminders   - fetch reminder config for current tenant
POST /api/settings/reminders   - save reminder config

Config schema stored in `settings` container:
{
    "id":                  "<tenant_id>:reminder_settings",
    "type":                "reminder_settings",
    "tenant_id":           "<tenant_id>",
    "reminders_enabled":   true,
    "before_due_days":     [3],
    "after_due_days":      [1, 3, 7],
    "updated_at":          "..."
}
"""
from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import settings_container
from datetime import datetime

reminders_blueprint = Blueprint('reminders', __name__)

DEFAULT_CONFIG = {
    'reminders_enabled': True,
    'before_due_days': [3],
    'after_due_days': [1, 3, 7],
}


def _get_config(tenant_id):
    doc_id = f"{tenant_id}:reminder_settings"
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
        'id':        doc_id,
        'type':      'reminder_settings',
        'tenant_id': tenant_id,
    }


@reminders_blueprint.route('/settings/reminders', methods=['GET'])
def get_reminder_settings():
    """Fetch payment reminder configuration for the current tenant."""
    try:
        cfg = _get_config(request.tenant_id)
        safe = {k: v for k, v in cfg.items() if not k.startswith('_')}
        return jsonify(safe), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reminders_blueprint.route('/settings/reminders', methods=['POST'])
def save_reminder_settings():
    """Save payment reminder configuration for the current tenant."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        cfg = _get_config(request.tenant_id)

        cfg['reminders_enabled'] = bool(data.get('reminders_enabled', cfg.get('reminders_enabled', True)))

        before = data.get('before_due_days', cfg.get('before_due_days', [3]))
        after  = data.get('after_due_days',  cfg.get('after_due_days',  [1, 3, 7]))

        # Validate: integers 1-30 only
        cfg['before_due_days'] = sorted({int(d) for d in before if 1 <= int(d) <= 30})
        cfg['after_due_days']  = sorted({int(d) for d in after  if 1 <= int(d) <= 30})
        cfg['updated_at']      = datetime.utcnow().isoformat()

        settings_container.upsert_item(body=cfg)

        safe = {k: v for k, v in cfg.items() if not k.startswith('_')}
        return jsonify({'message': 'Reminder settings saved successfully.', 'settings': safe}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

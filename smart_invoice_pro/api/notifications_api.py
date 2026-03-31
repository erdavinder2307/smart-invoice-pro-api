from flask import Blueprint, jsonify, request
from flasgger import swag_from
from datetime import datetime
from smart_invoice_pro.utils.cosmos_client import notifications_container
from azure.cosmos import exceptions as cosmos_exceptions

notifications_blueprint = Blueprint("notifications", __name__)

# ── List notifications ────────────────────────────────────────────────────────

@notifications_blueprint.route("/notifications", methods=["GET"])
@swag_from({
    "tags": ["Notifications"],
    "parameters": [
        {"name": "limit", "in": "query", "type": "integer", "default": 50},
        {"name": "unread_only", "in": "query", "type": "boolean", "default": False},
    ],
    "responses": {
        "200": {"description": "List of notifications"},
    },
})
def list_notifications():
    tenant_id = request.tenant_id
    limit = min(int(request.args.get("limit", 50)), 200)
    unread_only = request.args.get("unread_only", "false").lower() == "true"

    try:
        if unread_only:
            query = (
                "SELECT * FROM c WHERE c.tenant_id = @tid AND c.is_read = false "
                "ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
            )
        else:
            query = (
                "SELECT * FROM c WHERE c.tenant_id = @tid "
                "ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
            )

        params = [
            {"name": "@tid", "value": tenant_id},
            {"name": "@limit", "value": limit},
        ]
        items = list(
            notifications_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        unread_count = sum(1 for n in items if not n.get("is_read", False))

        return jsonify({"notifications": items, "unread_count": unread_count}), 200

    except Exception as exc:
        return jsonify({"error": f"Failed to fetch notifications: {str(exc)}"}), 500


# ── Mark single notification as read ─────────────────────────────────────────

@notifications_blueprint.route("/notifications/<notification_id>/read", methods=["PUT"])
def mark_notification_read(notification_id):
    tenant_id = request.tenant_id

    try:
        item = notifications_container.read_item(
            item=notification_id, partition_key=tenant_id
        )
        item["is_read"] = True
        item["read_at"] = datetime.utcnow().isoformat()
        notifications_container.replace_item(item=notification_id, body=item)
        return jsonify({"message": "Notification marked as read"}), 200

    except cosmos_exceptions.CosmosResourceNotFoundError:
        return jsonify({"error": "Notification not found"}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to update notification: {str(exc)}"}), 500


# ── Mark all notifications as read ───────────────────────────────────────────

@notifications_blueprint.route("/notifications/read-all", methods=["PUT"])
def mark_all_read():
    tenant_id = request.tenant_id

    try:
        query = "SELECT * FROM c WHERE c.tenant_id = @tid AND c.is_read = false"
        params = [{"name": "@tid", "value": tenant_id}]
        unread_items = list(
            notifications_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        now = datetime.utcnow().isoformat()
        updated = 0
        for item in unread_items:
            item["is_read"] = True
            item["read_at"] = now
            try:
                notifications_container.replace_item(item=item["id"], body=item)
                updated += 1
            except Exception:
                pass

        return jsonify({"message": f"Marked {updated} notifications as read"}), 200

    except Exception as exc:
        return jsonify({"error": f"Failed to mark all read: {str(exc)}"}), 500

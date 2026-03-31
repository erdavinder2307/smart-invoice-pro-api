from flask import Blueprint, jsonify, request
from flasgger import swag_from
from smart_invoice_pro.utils.cosmos_client import audit_logs_container
from smart_invoice_pro.api.roles_api import require_role

audit_logs_blueprint = Blueprint("audit_logs", __name__)

# ── GET /api/audit-logs ───────────────────────────────────────────────────────

@audit_logs_blueprint.route("/audit-logs", methods=["GET"])
@require_role("Admin")
@swag_from({
    "tags": ["Audit Logs"],
    "parameters": [
        {"name": "entity_type", "in": "query", "type": "string",  "description": "invoice|customer|payment|user"},
        {"name": "entity_id",   "in": "query", "type": "string"},
        {"name": "user_id",     "in": "query", "type": "string"},
        {"name": "action",      "in": "query", "type": "string",  "description": "create|update|delete"},
        {"name": "from_date",   "in": "query", "type": "string",  "description": "ISO date (YYYY-MM-DD)"},
        {"name": "to_date",     "in": "query", "type": "string",  "description": "ISO date (YYYY-MM-DD)"},
        {"name": "page",        "in": "query", "type": "integer", "default": 0},
        {"name": "limit",       "in": "query", "type": "integer", "default": 50},
    ],
    "responses": {
        "200": {"description": "Paginated audit log entries"},
        "403": {"description": "Admin access required"},
    },
})
def list_audit_logs():
    tenant_id = request.tenant_id

    # Parse query params
    entity_type = request.args.get("entity_type", "").strip()
    entity_id   = request.args.get("entity_id", "").strip()
    user_id     = request.args.get("user_id", "").strip()
    action      = request.args.get("action", "").strip()
    from_date   = request.args.get("from_date", "").strip()
    to_date     = request.args.get("to_date", "").strip()
    try:
        page  = max(0, int(request.args.get("page", 0)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        page, limit = 0, 50

    # Build WHERE clauses
    conditions = ["c.tenant_id = @tid"]
    params: list = [{"name": "@tid", "value": tenant_id}]

    if entity_type:
        conditions.append("c.entity_type = @entity_type")
        params.append({"name": "@entity_type", "value": entity_type})

    if entity_id:
        conditions.append("c.entity_id = @entity_id")
        params.append({"name": "@entity_id", "value": entity_id})

    if user_id:
        conditions.append("c.user_id = @filter_user_id")
        params.append({"name": "@filter_user_id", "value": user_id})

    if action:
        conditions.append("c.action = @action")
        params.append({"name": "@action", "value": action})

    if from_date:
        conditions.append("c.timestamp >= @from_date")
        params.append({"name": "@from_date", "value": from_date})

    if to_date:
        # to_date is inclusive: append 'T23:59:59' to cover the full day
        conditions.append("c.timestamp <= @to_date")
        params.append({"name": "@to_date", "value": to_date + "T23:59:59"})

    where_sql = " AND ".join(conditions)
    offset = page * limit

    try:
        # Count total matching records
        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
        total_list = list(
            audit_logs_container.query_items(
                query=count_query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        total = int(total_list[0]) if total_list else 0

        # Fetch paginated results
        data_query = (
            f"SELECT * FROM c WHERE {where_sql} "
            f"ORDER BY c.timestamp DESC "
            f"OFFSET {offset} LIMIT {limit}"
        )
        items = list(
            audit_logs_container.query_items(
                query=data_query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        # Strip internal Cosmos fields from log entries before returning
        clean_items = []
        for entry in items:
            clean_items.append({
                k: v for k, v in entry.items()
                if not k.startswith("_")
            })

        return jsonify({
            "logs": clean_items,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, -(-total // limit)),  # ceiling division
        }), 200

    except Exception as exc:
        return jsonify({"error": f"Failed to fetch audit logs: {str(exc)}"}), 500

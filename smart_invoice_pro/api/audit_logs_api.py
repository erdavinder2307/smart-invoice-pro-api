from flask import Blueprint, jsonify, request
from flasgger import swag_from
from smart_invoice_pro.utils.cosmos_client import audit_logs_container
from smart_invoice_pro.api.roles_api import require_role

audit_logs_blueprint = Blueprint("audit_logs", __name__)


def _clean_entry(entry):
    safe = {k: v for k, v in entry.items() if not k.startswith("_")}

    # Normalize legacy/new schemas for API consumers
    before = safe.get("before")
    after = safe.get("after")
    if before is None and isinstance(safe.get("changes"), dict):
        before = safe["changes"].get("before")
    if after is None and isinstance(safe.get("changes"), dict):
        after = safe["changes"].get("after")

    safe["entity"] = safe.get("entity") or safe.get("entity_type")
    safe["created_at"] = safe.get("created_at") or safe.get("timestamp")
    safe["before"] = before
    safe["after"] = after
    return safe

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
    entity = request.args.get("entity", "").strip() or entity_type
    entity_id   = request.args.get("entity_id", "").strip()
    user_id     = request.args.get("user_id", "").strip()
    action      = request.args.get("action", "").strip().upper()
    from_date   = request.args.get("from_date", "").strip() or request.args.get("start_date", "").strip()
    to_date     = request.args.get("to_date", "").strip() or request.args.get("end_date", "").strip()
    search      = request.args.get("search", "").strip().lower()
    try:
        page  = max(0, int(request.args.get("page", 0)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        page, limit = 0, 50

    # Build WHERE clauses
    conditions = ["c.tenant_id = @tid"]
    params: list = [{"name": "@tid", "value": tenant_id}]

    if entity:
        conditions.append("(c.entity = @entity OR c.entity_type = @entity)")
        params.append({"name": "@entity", "value": entity.lower()})

    if entity_id:
        conditions.append("c.entity_id = @entity_id")
        params.append({"name": "@entity_id", "value": entity_id})

    if user_id:
        conditions.append("c.user_id = @filter_user_id")
        params.append({"name": "@filter_user_id", "value": user_id})

    if action:
        conditions.append("UPPER(c.action) = @action")
        params.append({"name": "@action", "value": action})

    if from_date:
        conditions.append("(c.created_at >= @from_date OR c.timestamp >= @from_date)")
        params.append({"name": "@from_date", "value": from_date})

    if to_date:
        # to_date is inclusive: append 'T23:59:59' to cover the full day
        conditions.append("(c.created_at <= @to_date OR c.timestamp <= @to_date)")
        params.append({"name": "@to_date", "value": to_date + "T23:59:59"})

    if search:
        conditions.append("(CONTAINS(LOWER(c.entity_id), @search) OR CONTAINS(LOWER(c.user_id), @search) OR CONTAINS(LOWER(c.user_email), @search))")
        params.append({"name": "@search", "value": search})

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
            f"ORDER BY c.created_at DESC "
            f"OFFSET {offset} LIMIT {limit}"
        )
        items = list(
            audit_logs_container.query_items(
                query=data_query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        clean_items = [_clean_entry(entry) for entry in items]

        return jsonify({
            "logs": clean_items,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, -(-total // limit)),  # ceiling division
        }), 200

    except Exception as exc:
        return jsonify({"error": f"Failed to fetch audit logs: {str(exc)}"}), 500

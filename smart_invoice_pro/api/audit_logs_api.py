from flask import Blueprint, jsonify, make_response, request
from flasgger import swag_from
from smart_invoice_pro.utils.cosmos_client import audit_logs_container, domain_events_container
from smart_invoice_pro.utils.permission_checker import require_permission
from smart_invoice_pro.utils.activity_enrichment import enrich_audit_entries, enrich_audit_entry
from smart_invoice_pro.utils.domain_event_adapter import domain_event_to_activity
from smart_invoice_pro.utils.audit_query import parse_audit_filters, parse_pagination
from smart_invoice_pro.utils.audit_export import audit_rows_to_csv

audit_logs_blueprint = Blueprint("audit_logs", __name__)

EXPORT_MAX_ROWS = 10_000


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


def _fetch_audit_rows(*, tenant_id=None, page=0, limit=50):
    conditions, params = parse_audit_filters(tenant_id=tenant_id)
    where_sql = " AND ".join(conditions)
    offset = page * limit

    count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
    total_list = list(
        audit_logs_container.query_items(
            query=count_query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    total = int(total_list[0]) if total_list else 0

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
    return total, items


def _list_activity_logs():
    """Shared handler for tenant activity / audit log queries."""
    page, limit = parse_pagination()
    total, items = _fetch_audit_rows(tenant_id=request.tenant_id, page=page, limit=limit)
    clean_items = enrich_audit_entries([_clean_entry(entry) for entry in items])

    return jsonify({
        "logs": clean_items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }), 200


def _export_activity_logs():
    """CSV export for current tenant with active filters."""
    _, items = _fetch_audit_rows(
        tenant_id=request.tenant_id,
        page=0,
        limit=EXPORT_MAX_ROWS,
    )
    clean_items = enrich_audit_entries([_clean_entry(entry) for entry in items])
    csv_data = audit_rows_to_csv(clean_items)
    response = make_response(csv_data)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=activity-export.csv"
    return response


# ── GET /api/audit-logs ───────────────────────────────────────────────────────

@audit_logs_blueprint.route("/audit-logs", methods=["GET"])
@require_permission("audit_logs", "view")
@swag_from({
    "tags": ["Audit Logs"],
    "parameters": [
        {"name": "entity_type", "in": "query", "type": "string"},
        {"name": "entity_id",   "in": "query", "type": "string"},
        {"name": "user_id",     "in": "query", "type": "string"},
        {"name": "action",      "in": "query", "type": "string"},
        {"name": "category",    "in": "query", "type": "string", "description": "financial|security|settings|banking|system"},
        {"name": "risk_level",  "in": "query", "type": "string", "description": "low|medium|high"},
        {"name": "module",      "in": "query", "type": "string"},
        {"name": "search",      "in": "query", "type": "string"},
        {"name": "from_date",   "in": "query", "type": "string"},
        {"name": "to_date",     "in": "query", "type": "string"},
        {"name": "page",        "in": "query", "type": "integer", "default": 0},
        {"name": "limit",       "in": "query", "type": "integer", "default": 50},
    ],
    "responses": {
        "200": {"description": "Paginated audit log entries"},
        "403": {"description": "Permission denied"},
    },
})
def list_audit_logs():
    try:
        return _list_activity_logs()
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch audit logs: {str(exc)}"}), 500


@audit_logs_blueprint.route("/audit-logs/export", methods=["GET"])
@require_permission("audit_logs", "view")
@swag_from({
    "tags": ["Audit Logs"],
    "responses": {
        "200": {"description": "CSV export of filtered audit logs"},
        "403": {"description": "Permission denied"},
    },
})
def export_audit_logs():
    try:
        return _export_activity_logs()
    except Exception as exc:
        return jsonify({"error": f"Failed to export audit logs: {str(exc)}"}), 500


# ── GET /api/activity — Activity Center alias ────────────────────────────────

@audit_logs_blueprint.route("/activity", methods=["GET"])
@require_permission("audit_logs", "view")
@swag_from({
    "tags": ["Activity"],
    "responses": {
        "200": {"description": "Paginated activity feed (alias of audit-logs)"},
        "403": {"description": "Permission denied"},
    },
})
def list_activity():
    try:
        return _list_activity_logs()
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch activity: {str(exc)}"}), 500


@audit_logs_blueprint.route("/activity/export", methods=["GET"])
@require_permission("audit_logs", "view")
@swag_from({
    "tags": ["Activity"],
    "responses": {
        "200": {"description": "CSV export of filtered activity feed"},
        "403": {"description": "Permission denied"},
    },
})
def export_activity():
    try:
        return _export_activity_logs()
    except Exception as exc:
        return jsonify({"error": f"Failed to export activity: {str(exc)}"}), 500


def _fetch_entity_audit_logs(tenant_id: str, entity: str, entity_id: str):
    """Audit log rows for a single entity (includes legacy payment tags on invoices)."""
    conditions = ["c.tenant_id = @tid", "c.entity_id = @eid"]
    params = [
        {"name": "@tid", "value": tenant_id},
        {"name": "@eid", "value": entity_id},
    ]

    if entity == "invoice":
        conditions.append("(c.entity = @entity OR c.entity_type = @entity OR c.entity = 'payment')")
    else:
        conditions.append("(c.entity = @entity OR c.entity_type = @entity)")
    params.append({"name": "@entity", "value": entity.lower()})

    where_sql = " AND ".join(conditions)
    query = f"SELECT * FROM c WHERE {where_sql} ORDER BY c.created_at DESC"
    return list(
        audit_logs_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )


def _fetch_entity_domain_events(tenant_id: str, entity: str, entity_id: str):
    query = (
        "SELECT * FROM c WHERE c.tenant_id = @tid AND c.entity_id = @eid "
        "AND (c.entity_type = @entity OR NOT IS_DEFINED(c.entity_type)) "
        "ORDER BY c.created_at DESC"
    )
    return list(
        domain_events_container.query_items(
            query=query,
            parameters=[
                {"name": "@tid", "value": tenant_id},
                {"name": "@eid", "value": entity_id},
                {"name": "@entity", "value": entity.lower()},
            ],
            enable_cross_partition_query=True,
        )
    )


# ── GET /api/activity/entity — unified entity timeline ───────────────────────

@audit_logs_blueprint.route("/activity/entity", methods=["GET"])
@require_permission("audit_logs", "view")
@swag_from({
    "tags": ["Activity"],
    "parameters": [
        {"name": "entity_type", "in": "query", "type": "string", "required": True},
        {"name": "entity_id",   "in": "query", "type": "string", "required": True},
        {"name": "limit",       "in": "query", "type": "integer", "default": 50},
        {"name": "include_domain_events", "in": "query", "type": "boolean", "default": True},
    ],
    "responses": {
        "200": {"description": "Merged audit + domain event timeline for one entity"},
        "400": {"description": "Missing entity_type or entity_id"},
    },
})
def list_entity_activity():
    tenant_id = request.tenant_id
    entity = (request.args.get("entity_type") or request.args.get("entity") or "").strip().lower()
    entity_id = request.args.get("entity_id", "").strip()
    include_domain = request.args.get("include_domain_events", "true").strip().lower() not in (
        "false", "0", "no",
    )

    if not entity or not entity_id:
        return jsonify({"error": "entity_type and entity_id are required"}), 400

    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50

    try:
        audit_rows = [_clean_entry(row) for row in _fetch_entity_audit_logs(tenant_id, entity, entity_id)]
        audit_rows = enrich_audit_entries(audit_rows)

        merged = list(audit_rows)
        if include_domain:
            for row in _fetch_entity_domain_events(tenant_id, entity, entity_id):
                merged.append(enrich_audit_entry(domain_event_to_activity(row)))

        merged.sort(key=lambda item: item.get("created_at") or item.get("timestamp") or "", reverse=True)
        merged = merged[:limit]

        return jsonify({
            "logs": merged,
            "total": len(merged),
            "entity": entity,
            "entity_id": entity_id,
        }), 200
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch entity activity: {str(exc)}"}), 500

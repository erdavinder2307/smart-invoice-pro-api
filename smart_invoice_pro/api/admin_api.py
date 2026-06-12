"""
Super Admin API
───────────────
Endpoints under /api/admin/ for platform-wide tenant, user, feature-flag,
and system stats management.  Every route requires ``is_super_admin`` in the JWT.
"""
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from smart_invoice_pro.api.auth_middleware import super_admin_required
from smart_invoice_pro.utils.cosmos_client import (
    tenants_container,
    users_container,
    feature_flags_container,
    audit_logs_container,
)
from smart_invoice_pro.utils.audit_logger import get_audit_write_stats, log_audit_event
from smart_invoice_pro.utils.activity_enrichment import enrich_admin_audit_entries
from smart_invoice_pro.utils.audit_export import audit_rows_to_csv
from smart_invoice_pro.utils.audit_query import parse_audit_filters, parse_pagination
from smart_invoice_pro.utils.audit_retention import archive_expired_audit_logs, retention_days
from smart_invoice_pro.utils.tenant_service import create_tenant_doc, VALID_TENANT_PLANS

admin_blueprint = Blueprint("admin", __name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

VALID_TENANT_STATUSES = {"active", "inactive", "suspended"}
VALID_USER_STATUSES = {"active", "inactive", "suspended"}

_SENSITIVE_USER_FIELDS = {
    "password", "hashed_pw", "portal_password", "portal_token",
    "token", "refresh_token",
    "_rid", "_self", "_etag", "_attachments", "_ts",
}


def _sanitize_user(doc: dict) -> dict:
    """Return a copy of the user document without sensitive / Cosmos fields."""
    return {k: v for k, v in doc.items() if k not in _SENSITIVE_USER_FIELDS}


def _sanitize_doc(doc: dict) -> dict:
    """Strip Cosmos internal fields from any document."""
    cosmos_keys = {"_rid", "_self", "_etag", "_attachments", "_ts"}
    return {k: v for k, v in doc.items() if k not in cosmos_keys}


def _admin_user_id():
    return getattr(request, "user_id", None)


def _admin_tenant_id():
    return getattr(request, "tenant_id", None)


def _log_platform_audit(entity_type: str, action: str, entity_id, before, after):
    """Write an audit record tagged as a platform-admin action."""
    log_audit_event({
        "entity": entity_type,
        "action": action,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "user_id": _admin_user_id(),
        "tenant_id": _admin_tenant_id(),
        "metadata": {"source": "platform_admin"},
    })


def _clean_audit_entry(entry: dict) -> dict:
    safe = _sanitize_doc(entry)
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


# ═════════════════════════════════════════════════════════════════════════════
# TENANT MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

@admin_blueprint.route("/admin/tenants", methods=["POST"])
@super_admin_required
def create_tenant():
    """Provision a new tenant organization."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    plan = (data.get("plan") or "trial").strip().lower()
    if plan not in VALID_TENANT_PLANS:
        return jsonify({
            "error": f"Invalid plan. Must be one of: {', '.join(sorted(VALID_TENANT_PLANS))}"
        }), 400

    status = (data.get("status") or "active").strip().lower()
    if status not in VALID_TENANT_STATUSES:
        return jsonify({
            "error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_TENANT_STATUSES))}"
        }), 400

    try:
        tenant = create_tenant_doc(
            name=name,
            plan=plan,
            status=status,
            owner_user_id=(data.get("owner_user_id") or "").strip() or None,
        )
    except ValueError as exc:
        message = str(exc)
        if "already exists" in message.lower():
            return jsonify({"error": message}), 409
        return jsonify({"error": message}), 400

    _log_platform_audit("tenant", "create", tenant["id"], None, tenant)
    return jsonify(_sanitize_doc(tenant)), 201


@admin_blueprint.route("/admin/tenants", methods=["GET"])
@super_admin_required
def list_tenants():
    """List all tenants with pagination."""
    try:
        page = max(0, int(request.args.get("page", 0)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        page, limit = 0, 50

    offset = page * limit

    count_items = list(tenants_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    total = int(count_items[0]) if count_items else 0

    items = list(tenants_container.query_items(
        query=f"SELECT * FROM c ORDER BY c.created_at DESC OFFSET {offset} LIMIT {limit}",
        enable_cross_partition_query=True,
    ))

    return jsonify({
        "tenants": [_sanitize_doc(t) for t in items],
        "total": total,
        "page": page,
        "limit": limit,
    }), 200


@admin_blueprint.route("/admin/tenants/<tenant_id>", methods=["GET"])
@super_admin_required
def get_tenant(tenant_id):
    """Get a single tenant by ID."""
    items = list(tenants_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(_sanitize_doc(items[0])), 200


@admin_blueprint.route("/admin/tenants/<tenant_id>/status", methods=["PATCH"])
@super_admin_required
def update_tenant_status(tenant_id):
    """Activate / deactivate / suspend a tenant."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()
    if new_status not in VALID_TENANT_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_TENANT_STATUSES))}"}), 400

    items = list(tenants_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "Tenant not found"}), 404

    tenant = items[0]
    before = dict(tenant)
    tenant["status"] = new_status
    tenant["updated_at"] = datetime.utcnow().isoformat()
    tenants_container.replace_item(item=tenant["id"], body=tenant)

    _log_platform_audit("tenant", "update_status", tenant_id, before, tenant)
    return jsonify(_sanitize_doc(tenant)), 200


@admin_blueprint.route("/admin/tenants/<tenant_id>", methods=["DELETE"])
@super_admin_required
def delete_tenant(tenant_id):
    """Soft-delete a tenant (sets status to 'deleted')."""
    items = list(tenants_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "Tenant not found"}), 404

    tenant = items[0]
    before = dict(tenant)
    tenant["status"] = "deleted"
    tenant["deleted_at"] = datetime.utcnow().isoformat()
    tenant["updated_at"] = datetime.utcnow().isoformat()
    tenants_container.replace_item(item=tenant["id"], body=tenant)

    _log_platform_audit("tenant", "soft_delete", tenant_id, before, tenant)
    return jsonify({"message": "Tenant deleted", "id": tenant_id}), 200


# ═════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

@admin_blueprint.route("/admin/users", methods=["GET"])
@super_admin_required
def list_users():
    """List all users across all tenants with pagination."""
    try:
        page = max(0, int(request.args.get("page", 0)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        page, limit = 0, 50

    offset = page * limit

    count_items = list(users_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    total = int(count_items[0]) if count_items else 0

    items = list(users_container.query_items(
        query=f"SELECT * FROM c ORDER BY c.created_at DESC OFFSET {offset} LIMIT {limit}",
        enable_cross_partition_query=True,
    ))

    return jsonify({
        "users": [_sanitize_user(u) for u in items],
        "total": total,
        "page": page,
        "limit": limit,
    }), 200


@admin_blueprint.route("/admin/users/<user_id>/status", methods=["PATCH"])
@super_admin_required
def update_user_status(user_id):
    """Activate / deactivate / suspend a user."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()
    if new_status not in VALID_USER_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(VALID_USER_STATUSES))}"}), 400

    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "User not found"}), 404

    user = items[0]
    before = dict(user)
    user["status"] = new_status
    user["updated_at"] = datetime.utcnow().isoformat()
    users_container.replace_item(item=user["id"], body=user)

    _log_platform_audit("user", "update_status", user_id, before, user)
    return jsonify(_sanitize_user(user)), 200


@admin_blueprint.route("/admin/users/<user_id>/reset-password", methods=["POST"])
@super_admin_required
def reset_user_password(user_id):
    """Reset a user's password (admin-initiated)."""
    data = request.get_json(silent=True) or {}
    new_password = (data.get("new_password") or "").strip()
    if not new_password or len(new_password) < 8:
        return jsonify({"error": "new_password is required and must be at least 8 characters"}), 400

    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "User not found"}), 404

    user = items[0]
    user["password"] = generate_password_hash(new_password, method="pbkdf2:sha256", salt_length=16)
    user["updated_at"] = datetime.utcnow().isoformat()
    users_container.replace_item(item=user["id"], body=user)

    _log_platform_audit(
        "user", "reset_password", user_id, None, {"password_changed": True},
    )
    return jsonify({"message": "Password reset successfully", "user_id": user_id}), 200


# ═════════════════════════════════════════════════════════════════════════════
# FEATURE FLAGS
# ═════════════════════════════════════════════════════════════════════════════

@admin_blueprint.route("/admin/feature-flags/<tenant_id>", methods=["GET"])
@super_admin_required
def get_feature_flags(tenant_id):
    """Get feature flags for a specific tenant."""
    items = list(feature_flags_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"tenant_id": tenant_id, "flags": {}}), 200
    return jsonify(_sanitize_doc(items[0])), 200


@admin_blueprint.route("/admin/feature-flags/<tenant_id>", methods=["POST"])
@super_admin_required
def create_feature_flags(tenant_id):
    """Create feature flags for a tenant (fails if already exist)."""
    data = request.get_json(silent=True) or {}
    flags = data.get("flags")
    if not isinstance(flags, dict):
        return jsonify({"error": "flags must be a JSON object"}), 400

    existing = list(feature_flags_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if existing:
        return jsonify({"error": "Feature flags already exist for this tenant. Use PATCH to update."}), 409

    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "flags": flags,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    feature_flags_container.create_item(body=doc)

    _log_platform_audit("feature_flags", "create", tenant_id, None, doc)
    return jsonify(_sanitize_doc(doc)), 201


@admin_blueprint.route("/admin/feature-flags/<tenant_id>", methods=["PATCH"])
@super_admin_required
def update_feature_flags(tenant_id):
    """Update (merge) feature flags for a tenant."""
    data = request.get_json(silent=True) or {}
    flags = data.get("flags")
    if not isinstance(flags, dict):
        return jsonify({"error": "flags must be a JSON object"}), 400

    items = list(feature_flags_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "No feature flags found for this tenant. Use POST to create."}), 404

    doc = items[0]
    before = dict(doc)
    doc["flags"].update(flags)
    doc["updated_at"] = datetime.utcnow().isoformat()
    feature_flags_container.replace_item(item=doc["id"], body=doc)

    _log_platform_audit("feature_flags", "update", tenant_id, before, doc)
    return jsonify(_sanitize_doc(doc)), 200


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT LOGS (CROSS-TENANT)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_admin_audit_rows(*, page=0, limit=50):
    scoped_tenant = (request.args.get("tenant_id") or "").strip() or None
    conditions, params = parse_audit_filters(tenant_id=scoped_tenant)
    where_sql = " AND ".join(conditions)
    offset = page * limit

    count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
    total_rows = list(
        audit_logs_container.query_items(
            query=count_query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    total = int(total_rows[0]) if total_rows else 0

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


@admin_blueprint.route("/admin/audit-logs", methods=["GET"])
@super_admin_required
def list_audit_logs_admin():
    """List audit logs across all tenants (super admin only)."""
    page, limit = parse_pagination()
    total, items = _fetch_admin_audit_rows(page=page, limit=limit)
    logs = enrich_admin_audit_entries([_clean_audit_entry(x) for x in items])

    return jsonify({
        "logs": logs,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }), 200


@admin_blueprint.route("/admin/audit-logs/export", methods=["GET"])
@super_admin_required
def export_audit_logs_admin():
    """CSV export of cross-tenant audit logs."""
    from flask import make_response

    _, items = _fetch_admin_audit_rows(page=0, limit=10_000)
    logs = enrich_admin_audit_entries([_clean_audit_entry(x) for x in items])
    csv_data = audit_rows_to_csv(logs, include_tenant=True)
    response = make_response(csv_data)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=platform-audit-export.csv"
    return response


@admin_blueprint.route("/admin/audit-stats", methods=["GET"])
@super_admin_required
def audit_stats_admin():
    """Audit write health and retention configuration."""
    return jsonify({
        "write_stats": get_audit_write_stats(),
        "retention_days": retention_days(),
    }), 200


@admin_blueprint.route("/admin/audit-retention/run", methods=["POST"])
@super_admin_required
def run_audit_retention_admin():
    """Archive expired audit logs (manual trigger for ops)."""
    tenant_id = (request.get_json(silent=True) or {}).get("tenant_id")
    result = archive_expired_audit_logs(tenant_id=tenant_id or None)
    return jsonify(result), 200


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM STATS
# ═════════════════════════════════════════════════════════════════════════════

@admin_blueprint.route("/admin/stats", methods=["GET"])
@super_admin_required
def system_stats():
    """Return high-level platform statistics."""
    total_users_q = list(users_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    total_users = int(total_users_q[0]) if total_users_q else 0

    active_users_q = list(users_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c WHERE (NOT IS_DEFINED(c.status) OR c.status = 'active')",
        enable_cross_partition_query=True,
    ))
    active_users = int(active_users_q[0]) if active_users_q else 0

    total_tenants_q = list(tenants_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    total_tenants = int(total_tenants_q[0]) if total_tenants_q else 0

    return jsonify({
        "total_users": total_users,
        "active_users": active_users,
        "total_tenants": total_tenants,
    }), 200


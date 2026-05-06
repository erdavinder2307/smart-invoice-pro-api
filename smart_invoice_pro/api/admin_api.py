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
from smart_invoice_pro.utils.audit_logger import log_audit

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

    log_audit(
        entity_type="tenant", action="update_status", entity_id=tenant_id,
        before=before, after=tenant,
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
    )
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

    log_audit(
        entity_type="tenant", action="soft_delete", entity_id=tenant_id,
        before=before, after=tenant,
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
    )
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

    log_audit(
        entity_type="user", action="update_status", entity_id=user_id,
        before=before, after=user,
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
    )
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

    log_audit(
        entity_type="user", action="reset_password", entity_id=user_id,
        before=None, after={"password_changed": True},
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
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

    log_audit(
        entity_type="feature_flags", action="create", entity_id=tenant_id,
        before=None, after=doc,
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
    )
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

    log_audit(
        entity_type="feature_flags", action="update", entity_id=tenant_id,
        before=before, after=doc,
        user_id=_admin_user_id(), tenant_id=_admin_tenant_id(),
    )
    return jsonify(_sanitize_doc(doc)), 200


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT LOGS (CROSS-TENANT)
# ═════════════════════════════════════════════════════════════════════════════

@admin_blueprint.route("/admin/audit-logs", methods=["GET"])
@super_admin_required
def list_audit_logs_admin():
    """List audit logs across all tenants (super admin only)."""
    tenant_id = (request.args.get("tenant_id") or "").strip()
    user_id = (request.args.get("user_id") or "").strip()
    entity = ((request.args.get("entity") or request.args.get("entity_type") or "").strip().lower())
    action = (request.args.get("action") or "").strip().upper()
    start_date = (request.args.get("start_date") or request.args.get("from_date") or "").strip()
    end_date = (request.args.get("end_date") or request.args.get("to_date") or "").strip()
    search = (request.args.get("search") or "").strip().lower()

    try:
        page = max(0, int(request.args.get("page", 0)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        page, limit = 0, 50

    conditions = ["1=1"]
    params = []

    if tenant_id:
        conditions.append("c.tenant_id = @tenant_id")
        params.append({"name": "@tenant_id", "value": tenant_id})
    if user_id:
        conditions.append("c.user_id = @user_id")
        params.append({"name": "@user_id", "value": user_id})
    if entity:
        conditions.append("(c.entity = @entity OR c.entity_type = @entity)")
        params.append({"name": "@entity", "value": entity})
    if action:
        conditions.append("UPPER(c.action) = @action")
        params.append({"name": "@action", "value": action})
    if start_date:
        conditions.append("(c.created_at >= @start_date OR c.timestamp >= @start_date)")
        params.append({"name": "@start_date", "value": start_date})
    if end_date:
        conditions.append("(c.created_at <= @end_date OR c.timestamp <= @end_date)")
        params.append({"name": "@end_date", "value": end_date + "T23:59:59"})
    if search:
        conditions.append("(CONTAINS(LOWER(c.entity_id), @search) OR CONTAINS(LOWER(c.user_id), @search) OR CONTAINS(LOWER(c.user_email), @search))")
        params.append({"name": "@search", "value": search})

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

    return jsonify({
        "logs": [_clean_audit_entry(x) for x in items],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }), 200


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


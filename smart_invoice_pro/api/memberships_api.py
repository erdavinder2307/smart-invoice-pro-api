"""
Memberships API
===============
Let a user from another organisation (e.g. a CA firm) work in a tenant's books
with one login.

Tenant Admin:
    GET    /api/settings/memberships          people from other organisations with access
    POST   /api/settings/memberships          { email, role_id }  grant access
    DELETE /api/settings/memberships/<id>     end access (takes effect on the next request)

Signed-in user:
    GET    /api/auth/organizations            own organisation + every one shared with them
    POST   /api/auth/switch-organization      { tenant_id }  new tokens for that organisation
"""

import datetime
import re
import uuid

from flask import Blueprint, jsonify, request

from smart_invoice_pro.api.roles_api import require_role
from smart_invoice_pro.utils.audit_logger import log_audit
from smart_invoice_pro.utils.cosmos_client import users_container
from smart_invoice_pro.utils.demo_guard import forbid_demo_settings_mutation
from smart_invoice_pro.utils import memberships as ms
from smart_invoice_pro.utils.rbac_resolver import fetch_account_user
from smart_invoice_pro.utils.tenant_service import get_tenant_by_id

memberships_blueprint = Blueprint("memberships", __name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "email": m.get("email"),
        "role": m.get("role"),
        "role_id": m.get("role_id"),
        "status": m.get("status"),
        "created_at": m.get("created_at"),
    }


def _tenant_name(tenant_id: str) -> str:
    try:
        tenant = get_tenant_by_id(tenant_id)
    except Exception:
        tenant = None
    return (tenant or {}).get("name") or ""


# ── Tenant Admin ──────────────────────────────────────────────────────────────

@memberships_blueprint.route("/settings/memberships", methods=["GET"])
@require_role("Admin")
def list_memberships():
    items = list(ms._container().query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid AND c.status = 'active'",
        parameters=[{"name": "@tid", "value": request.tenant_id}],
        enable_cross_partition_query=True,
    ))
    items.sort(key=lambda m: m.get("email") or "")
    return jsonify([_safe(m) for m in items]), 200


@memberships_blueprint.route("/settings/memberships", methods=["POST"])
@require_role("Admin")
@forbid_demo_settings_mutation("Sharing books with other organisations is disabled in the Interactive Workspace.")
def grant_membership():
    from smart_invoice_pro.api.roles_permissions_api import _get_role_by_id

    data = request.get_json(silent=True) or {}
    email = ms.normalize_email(data.get("email"))
    role_id = (data.get("role_id") or "").strip() if isinstance(data.get("role_id"), str) else ""
    tenant_id = request.tenant_id

    if not _EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"error": "Valid email is required"}), 400
    role_doc = _get_role_by_id(role_id, tenant_id) if role_id else None
    if not role_doc:
        return jsonify({"error": "role_id must be one of this organisation's roles"}), 400
    if role_doc.get("name") in ms.FORBIDDEN_MEMBER_ROLES:
        return jsonify({"error": "People from other organisations cannot be given the Admin role"}), 400

    own_users = list(users_container.query_items(
        query="SELECT c.id FROM c WHERE c.tenant_id = @tid AND LOWER(c.email) = @email",
        parameters=[{"name": "@tid", "value": tenant_id}, {"name": "@email", "value": email}],
        enable_cross_partition_query=True,
    ))
    if own_users:
        return jsonify({"error": "This person is already a user of this organisation"}), 409
    existing = list(ms._container().query_items(
        query="SELECT c.id FROM c WHERE c.tenant_id = @tid AND c.email = @email AND c.status = 'active'",
        parameters=[{"name": "@tid", "value": tenant_id}, {"name": "@email", "value": email}],
        enable_cross_partition_query=True,
    ))
    if existing:
        return jsonify({"error": "This email already has access"}), 409

    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "email": email,
        "user_id": None,
        "role": role_doc.get("name", ""),
        "role_id": role_doc.get("id"),
        "status": "active",
        "created_by": request.user_id,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    ms._container().create_item(body=doc)
    log_audit("membership", "create", doc["id"], None, _safe(doc),
              user_id=request.user_id, tenant_id=tenant_id)
    # Same answer whether or not a Books account has this email yet.
    return jsonify(_safe(doc)), 201


@memberships_blueprint.route("/settings/memberships/<membership_id>", methods=["DELETE"])
@require_role("Admin")
@forbid_demo_settings_mutation("Sharing books with other organisations is disabled in the Interactive Workspace.")
def revoke_membership(membership_id):
    tenant_id = request.tenant_id
    doc = ms.get_membership(membership_id, tenant_id)
    if not doc or doc.get("status") != "active":
        return jsonify({"error": "Not found"}), 404
    before = _safe(doc)
    doc["status"] = "revoked"
    doc["revoked_at"] = datetime.datetime.utcnow().isoformat()
    doc["revoked_by"] = request.user_id
    ms._container().replace_item(item=doc["id"], body=doc)
    log_audit("membership", "delete", doc["id"], before, _safe(doc),
              user_id=request.user_id, tenant_id=tenant_id)
    return jsonify({"message": "Access removed"}), 200


# ── Signed-in user ────────────────────────────────────────────────────────────

@memberships_blueprint.route("/auth/organizations", methods=["GET"])
def list_my_organizations():
    user = fetch_account_user(request.user_id)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    home = ms.home_tenant_id(user)
    current = request.tenant_id
    orgs = [{
        "tenant_id": home,
        "name": _tenant_name(home),
        "role": user.get("role", ""),
        "is_home": True,
        "current": current == home,
    }]
    if not user.get("is_demo_user"):
        for m in ms.list_memberships_for_user(user):
            orgs.append({
                "tenant_id": m["tenant_id"],
                "name": _tenant_name(m["tenant_id"]),
                "role": m.get("role", ""),
                "is_home": False,
                "current": current == m["tenant_id"],
            })
    return jsonify(orgs), 200


@memberships_blueprint.route("/auth/switch-organization", methods=["POST"])
def switch_organization():
    from smart_invoice_pro.api.routes import _issue_auth_tokens

    data = request.get_json(silent=True) or {}
    target = data.get("tenant_id")
    if not isinstance(target, str) or not target.strip():
        return jsonify({"error": "tenant_id is required"}), 400
    target = target.strip()

    user = fetch_account_user(request.user_id)
    if not user or user.get("is_demo_user"):
        return jsonify({"error": "Unauthorized"}), 401
    if (user.get("status") or "").lower() == "suspended" or user.get("is_active") is False:
        return jsonify({"error": "Unauthorized"}), 401

    membership = None
    if target != ms.home_tenant_id(user):
        membership = next(
            (m for m in ms.list_memberships_for_user(user) if m.get("tenant_id") == target),
            None,
        )
        if not membership:
            # Same answer for "no such organisation" and "not shared with you".
            return jsonify({"error": "You do not have access to this organisation"}), 403

    access_token, refresh_token, tenant_id, user_id = _issue_auth_tokens(user, membership)
    if membership:
        log_audit("membership", "switch_in", membership["id"], None,
                  {"id": membership["id"], "user_id": user_id},
                  user_id=user_id, tenant_id=tenant_id)
    return jsonify({
        "access_token": access_token,
        "token": access_token,
        "refresh_token": refresh_token,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": (membership or user).get("role", ""),
    }), 200

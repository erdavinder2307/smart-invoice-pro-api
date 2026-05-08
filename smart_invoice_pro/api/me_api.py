"""
User Identity (Me) API
======================
Properly separates user identity from organisation profile.

GET    /api/me                    – full identity (user record + identity doc + org name)
PUT    /api/me                    – update personal identity fields only
GET    /api/me/preferences        – user UI/notification preferences
PUT    /api/me/preferences        – update preferences
PUT    /api/me/password           – change password (requires current password)
GET    /api/me/sessions           – list active refresh-token sessions
DELETE /api/me/sessions/<id>      – revoke a session

All endpoints require a valid JWT Bearer token.

Data stored in `users` container (partition key /userid):
  - type == (none)               → base user record  (username, password, role, …)
  - type == 'user_identity'      → personal identity (full_name, phone, designation, …)
  - type == 'user_preferences'   → UI + notification prefs
"""

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from smart_invoice_pro.utils.cosmos_client import (
    refresh_tokens_container,
    settings_container,
    users_container,
)
from smart_invoice_pro.utils.audit_logger import log_audit_event

me_blueprint = Blueprint("me", __name__)

# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe(doc: dict) -> dict:
    """Strip Cosmos-internal and sensitive fields before returning."""
    skip = {"password", "_rid", "_self", "_etag", "_attachments", "_ts"}
    return {k: v for k, v in doc.items() if k not in skip}


def _get_user_record(user_id: str) -> dict:
    """Fetch base user record (has role, username/email, password hash)."""
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    # Filter to the plain user record (no type or type == 'user')
    for item in items:
        t = item.get("type", "")
        if t in ("", "user", None):
            return item
    return items[0] if items else {}


def _get_identity(user_id: str) -> dict:
    """Fetch user_identity document (personal fields)."""
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user_identity' AND c.user_id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else {}


def _get_old_profile(user_id: str) -> dict:
    """Backward-compat: fetch old user_profile document if it exists."""
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user_profile' AND c.user_id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else {}


def _get_org_name(tenant_id: str) -> str:
    """Fetch organisation display name from settings container."""
    try:
        items = list(settings_container.query_items(
            query=(
                "SELECT c.organization_name FROM c "
                "WHERE c.tenant_id = @tid AND c.type = 'organization_profile'"
            ),
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        if items:
            return items[0].get("organization_name", "") or ""
    except Exception:
        pass
    return ""


# ── GET /api/me ───────────────────────────────────────────────────────────────

@me_blueprint.route("/me", methods=["GET"])
def get_me():
    """Return a merged user identity object for the authenticated user."""
    user_id = request.user_id
    tenant_id = request.tenant_id

    user_record = _get_user_record(user_id)
    identity = _get_identity(user_id)
    old_profile = _get_old_profile(user_id)
    org_name = _get_org_name(tenant_id)

    # Prefer new identity doc values, fall back to old profile, then user record
    full_name = (
        identity.get("full_name")
        or old_profile.get("name")
        or user_record.get("name")
        or ""
    )
    email = (
        identity.get("email")
        or user_record.get("email")
        or user_record.get("username")
        or ""
    )

    return jsonify({
        "id":                  user_id,
        "tenant_id":           tenant_id,
        "username":            user_record.get("username", ""),
        "email":               email,
        "role":                user_record.get("role", "Sales"),
        "is_super_admin":      bool(user_record.get("is_super_admin", False)),
        # Personal identity
        "full_name":           full_name,
        "display_name":        identity.get("display_name") or full_name,
        "avatar_url":          identity.get("avatar_url", ""),
        "phone":               identity.get("phone") or old_profile.get("phone", ""),
        "designation":         identity.get("designation", ""),
        "department":          identity.get("department", ""),
        "timezone":            identity.get("timezone", "Asia/Kolkata"),
        "language":            identity.get("language", "en"),
        "date_format":         identity.get("date_format", "DD/MM/YYYY"),
        # Organisation membership (read-only)
        "organization_name":   org_name,
        "joined_at":           user_record.get("created_at", ""),
        "membership_status":   "active",
        # Security summary
        "created_at":          user_record.get("created_at", ""),
        "last_login_at":       identity.get("last_login_at", ""),
        "password_changed_at": user_record.get("password_changed_at", ""),
    }), 200


# ── PUT /api/me ───────────────────────────────────────────────────────────────

@me_blueprint.route("/me", methods=["PUT"])
def update_me():
    """Update personal identity fields for the authenticated user."""
    user_id = request.user_id
    tenant_id = request.tenant_id
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    now = datetime.utcnow().isoformat()

    # Load or initialise identity doc
    identity = _get_identity(user_id)
    before = _safe(identity)

    if not identity:
        identity = {
            "id":         f"identity_{uuid.uuid4()}",
            "userid":     user_id,          # partition key field
            "type":       "user_identity",
            "user_id":    user_id,
            "tenant_id":  tenant_id,
            "created_at": now,
        }

    allowed = [
        "full_name", "display_name", "phone", "designation",
        "department", "avatar_url", "timezone", "language", "date_format",
    ]
    for field in allowed:
        if field in data:
            identity[field] = data[field]
    identity["updated_at"] = now

    users_container.upsert_item(body=identity)

    # Backward-compat: mirror full_name → old profile doc's `name` field
    if "full_name" in data:
        old_profile = _get_old_profile(user_id)
        if old_profile:
            old_profile["name"] = data["full_name"]
            old_profile["updated_at"] = now
            users_container.upsert_item(body=old_profile)
        # Also update base user record's `name` field for login response
        user_record = _get_user_record(user_id)
        if user_record:
            user_record["name"] = data["full_name"]
            users_container.upsert_item(body=user_record)

    log_audit_event({
        "action":    "USER_PROFILE_UPDATED",
        "entity":    "user",
        "entity_id": user_id,
        "before":    before,
        "after":     _safe(identity),
        "tenant_id": tenant_id,
        "user_id":   user_id,
    })

    return jsonify({"message": "Profile updated", "profile": _safe(identity)}), 200


# ── GET /api/me/preferences ───────────────────────────────────────────────────

@me_blueprint.route("/me/preferences", methods=["GET"])
def get_preferences():
    user_id = request.user_id

    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user_preferences' AND c.user_id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))

    if items:
        return jsonify(_safe(items[0])), 200

    # Return sensible defaults
    return jsonify({
        "user_id":              user_id,
        "theme":                "light",
        "timezone":             "Asia/Kolkata",
        "language":             "en",
        "date_format":          "DD/MM/YYYY",
        "currency_format":      "INR",
        "default_dashboard":    "main",
        "compact_mode":         False,
        "notification_preferences": {
            "email_notifications":    True,
            "workflow_notifications": True,
            "approval_notifications": True,
            "reminder_notifications": True,
            "operational_alerts":     True,
        },
    }), 200


# ── PUT /api/me/preferences ───────────────────────────────────────────────────

@me_blueprint.route("/me/preferences", methods=["PUT"])
def update_preferences():
    user_id = request.user_id
    tenant_id = request.tenant_id
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    now = datetime.utcnow().isoformat()

    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user_preferences' AND c.user_id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))

    before = {}
    if items:
        prefs = items[0]
        before = _safe(prefs)
    else:
        prefs = {
            "id":         f"prefs_{uuid.uuid4()}",
            "userid":     user_id,
            "type":       "user_preferences",
            "user_id":    user_id,
            "tenant_id":  tenant_id,
            "created_at": now,
        }

    allowed = [
        "theme", "timezone", "language", "date_format", "currency_format",
        "default_dashboard", "compact_mode", "notification_preferences",
    ]
    for field in allowed:
        if field in data:
            prefs[field] = data[field]
    prefs["updated_at"] = now

    users_container.upsert_item(body=prefs)

    action = (
        "USER_NOTIFICATION_SETTINGS_UPDATED"
        if "notification_preferences" in data
        else "USER_PREFERENCES_UPDATED"
    )
    log_audit_event({
        "action":    action,
        "entity":    "user",
        "entity_id": user_id,
        "before":    before,
        "after":     _safe(prefs),
        "tenant_id": tenant_id,
        "user_id":   user_id,
    })

    return jsonify({"message": "Preferences updated", "preferences": _safe(prefs)}), 200


# ── GET /api/me/sessions ──────────────────────────────────────────────────────

@me_blueprint.route("/me/sessions", methods=["GET"])
def get_sessions():
    user_id = request.user_id

    items = list(refresh_tokens_container.query_items(
        query="SELECT * FROM c WHERE c.user_id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))

    now = datetime.utcnow()
    current_token_id = getattr(request, "token_id", None)

    sessions = []
    for item in items:
        try:
            exp_dt = datetime.fromisoformat(item.get("expires_at", ""))
            if exp_dt < now:
                continue
        except Exception:
            continue

        browser = item.get("browser", "")
        os_name = item.get("os", "")
        device_type = item.get("device_type", "Desktop")
        device_name = item.get("device_name", "")
        is_legacy = not bool(browser)

        # Build a meaningful display name
        if not device_name:
            device_name = " on ".join(filter(None, [browser, os_name]))

        sessions.append({
            "id":              item.get("id"),
            "created_at":      item.get("created_at", ""),
            "expires_at":      item.get("expires_at", ""),
            "last_active_at":  item.get("last_active_at") or item.get("created_at", ""),
            "device":          device_name,
            "device_type":     device_type,
            "browser":         browser,
            "browser_version": item.get("browser_version", ""),
            "os":              os_name,
            "ip_address":      item.get("ip_address", ""),
            "is_current":      item.get("id") == current_token_id,
            "is_legacy":       is_legacy,
        })

    # Sort: current first, then newest
    sessions.sort(key=lambda s: (not s["is_current"], s["created_at"]), reverse=False)
    sessions.sort(key=lambda s: s["is_current"], reverse=True)
    return jsonify({"sessions": sessions}), 200


# ── DELETE /api/me/sessions/<session_id> ──────────────────────────────────────

@me_blueprint.route("/me/sessions/<session_id>", methods=["DELETE"])
def revoke_session(session_id):
    user_id = request.user_id
    tenant_id = request.tenant_id

    items = list(refresh_tokens_container.query_items(
        query="SELECT * FROM c WHERE c.id = @sid AND c.user_id = @uid",
        parameters=[
            {"name": "@sid", "value": session_id},
            {"name": "@uid", "value": user_id},
        ],
        enable_cross_partition_query=True,
    ))

    if not items:
        return jsonify({"error": "Session not found"}), 404

    record = items[0]
    try:
        refresh_tokens_container.delete_item(
            item=record["id"], partition_key=record["user_id"]
        )
    except Exception:
        return jsonify({"error": "Failed to revoke session"}), 500

    log_audit_event({
        "action":    "USER_SESSION_REVOKED",
        "entity":    "session",
        "entity_id": session_id,
        "before":    {"session_id": session_id},
        "after":     {"revoked": True},
        "tenant_id": tenant_id,
        "user_id":   user_id,
    })

    return jsonify({"message": "Session revoked"}), 200


# ── PUT /api/me/password ──────────────────────────────────────────────────────

@me_blueprint.route("/me/password", methods=["PUT"])
def change_password():
    user_id = request.user_id
    tenant_id = request.tenant_id
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    current_password = (data.get("current_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if not current_password or not new_password:
        return jsonify({"error": "current_password and new_password are required"}), 400

    if len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400

    user_record = _get_user_record(user_id)
    if not user_record:
        return jsonify({"error": "User not found"}), 404

    if not check_password_hash(user_record.get("password", ""), current_password):
        return jsonify({"error": "Current password is incorrect"}), 400

    user_record["password"] = generate_password_hash(
        new_password, method="pbkdf2:sha256", salt_length=16
    )
    user_record["password_changed_at"] = datetime.utcnow().isoformat()
    users_container.upsert_item(body=user_record)

    log_audit_event({
        "action":    "USER_PASSWORD_CHANGED",
        "entity":    "user",
        "entity_id": user_id,
        "before":    {"password_changed": False},
        "after":     {"password_changed": True},
        "tenant_id": tenant_id,
        "user_id":   user_id,
    })

    return jsonify({"message": "Password changed successfully"}), 200

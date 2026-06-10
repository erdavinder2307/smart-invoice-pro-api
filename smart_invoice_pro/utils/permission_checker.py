"""
permission_checker.py
=====================
Granular module+action permission enforcement for API endpoints.

Usage
-----
from smart_invoice_pro.utils.permission_checker import require_permission

@app.route('/api/invoices', methods=['POST'])
@require_permission('invoices', 'create')
def create_invoice():
    ...

How it works
------------
1. Reads request.user_id (set by auth_middleware.enforce_api_auth)
2. Looks up user document → gets role_id (and role name as fallback)
3. Loads the role document → reads permissions[module][action]
4. Admin role always passes
5. Returns 403 if permission is False or missing

Performance note
----------------
Each check makes up to 2 Cosmos point-reads (user + role). Both
documents are small and the hot path is O(1) key lookup after fetch.
A Redis/in-process cache can be added later without changing call sites.
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import jsonify, request

logger = logging.getLogger(__name__)

# Modules that Admin always has full access to (belt-and-suspenders)
_ADMIN_ROLES = frozenset({'Admin'})


def _get_user_permissions(user_id: str, tenant_id: str) -> tuple[bool, dict]:
    """
    Return (is_admin, permissions_dict) for the given user.

    is_admin  – True when the user's role is 'Admin' (bypasses all checks)
    permissions_dict – {module: {action: bool}} from the role document
    """
    from smart_invoice_pro.utils.cosmos_client import users_container, get_container

    # ── 1. Fetch user document ────────────────────────────────────────────────
    users = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid AND c.tenant_id = @tid",
        parameters=[
            {"name": "@uid", "value": user_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not users:
        return False, {}

    user = users[0]

    # Suspended / inactive users get nothing
    status = (user.get('status') or '').lower()
    if status == 'suspended' or user.get('is_active') is False:
        return False, {}

    role_name = user.get('role', '')
    role_id   = user.get('role_id')

    # Admin short-circuit
    if role_name in _ADMIN_ROLES:
        return True, {}

    # ── 2. Fetch role document ────────────────────────────────────────────────
    roles_container = get_container("roles", "/tenant_id")

    role_docs = []
    if role_id:
        role_docs = list(roles_container.query_items(
            query="SELECT * FROM c WHERE c.id = @rid AND c.tenant_id = @tid",
            parameters=[
                {"name": "@rid", "value": role_id},
                {"name": "@tid", "value": tenant_id},
            ],
            enable_cross_partition_query=True,
        ))

    # Fallback: look up by role name if role_id missing or stale
    if not role_docs and role_name:
        role_docs = list(roles_container.query_items(
            query="SELECT * FROM c WHERE c.name = @name AND c.tenant_id = @tid",
            parameters=[
                {"name": "@name", "value": role_name},
                {"name": "@tid",  "value": tenant_id},
            ],
            enable_cross_partition_query=True,
        ))

    if not role_docs:
        return False, {}

    permissions = role_docs[0].get('permissions') or {}
    return False, permissions


def require_permission(module: str, action: str):
    """
    Decorator: allow the request only if the authenticated user has
    `permissions[module][action] == True` (or is Admin).

    Must be applied AFTER JWT auth has already run (auth_middleware
    sets request.user_id and request.tenant_id via before_request).

    Returns 403 JSON if the permission is denied.
    Returns 401 JSON if the request context has no user_id (shouldn't
    happen in normal flow but handles edge cases cleanly).
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_id   = getattr(request, 'user_id',   None)
            tenant_id = getattr(request, 'tenant_id', None)

            if not user_id or not tenant_id:
                return jsonify({'error': 'Unauthorized'}), 401

            try:
                is_admin, permissions = _get_user_permissions(user_id, tenant_id)
            except Exception as exc:
                logger.error(f"permission_checker: error fetching permissions "
                             f"user={user_id} module={module} action={action}: {exc}")
                return jsonify({'error': 'Permission check failed'}), 500

            if is_admin:
                return fn(*args, **kwargs)

            if not permissions.get(module, {}).get(action, False):
                return jsonify({
                    'error': f'Forbidden — {module}.{action} permission required',
                    'module': module,
                    'action': action,
                }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator


def check_permission(module: str, action: str) -> bool:
    """
    Inline helper (not a decorator) — call inside a view function when
    the permission check depends on runtime conditions.

    Returns True if permitted, False otherwise.
    Does NOT raise or return an HTTP response.
    """
    user_id   = getattr(request, 'user_id',   None)
    tenant_id = getattr(request, 'tenant_id', None)
    if not user_id or not tenant_id:
        return False
    try:
        is_admin, permissions = _get_user_permissions(user_id, tenant_id)
        if is_admin:
            return True
        return bool(permissions.get(module, {}).get(action, False))
    except Exception:
        return False

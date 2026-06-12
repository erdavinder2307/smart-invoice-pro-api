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
2. Resolves user + role via rbac_resolver.resolve_user_permissions
3. Admin (by role string OR Admin role document via role_id) always passes
4. Returns 403 if permission is False or missing
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import jsonify, request

from smart_invoice_pro.utils.rbac_resolver import resolve_user_permissions

logger = logging.getLogger(__name__)


def _get_user_permissions(user_id: str, tenant_id: str) -> tuple[bool, dict]:
    """Backward-compatible wrapper around the shared RBAC resolver."""
    return resolve_user_permissions(user_id, tenant_id)


def require_permission(module: str, action: str):
    """
    Decorator: allow the request only if the authenticated user has
    `permissions[module][action] == True` (or is Admin).

    Must be applied AFTER JWT auth has already run (auth_middleware
    sets request.user_id and request.tenant_id via before_request).
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
                logger.error(
                    "permission_checker: error fetching permissions "
                    f"user={user_id} module={module} action={action}: {exc}"
                )
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
    """Inline helper — returns True if permitted, False otherwise."""
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

"""
rbac_resolver.py
================
Single source of truth for resolving a user's effective permissions.

Used by permission_checker (API decorators) and should stay aligned with
roles_permissions_api.get_my_permissions / _check_permission.
"""

from __future__ import annotations

from smart_invoice_pro.utils.cosmos_client import users_container, get_container

_ADMIN_ROLE_NAMES = frozenset({'Admin'})


def _is_account_user(doc: dict) -> bool:
    """True for login account records, not identity/profile side documents."""
    if not doc:
        return False
    doc_type = doc.get('type') or ''
    if doc_type in ('user_profile', 'user_identity', 'user_preferences'):
        return False
    if doc.get('permissions') is not None and doc.get('is_system_role') is not None:
        return False
    return bool(doc.get('password')) or bool(doc.get('username')) or (
        not doc_type and bool(doc.get('role'))
    )


def fetch_account_user(user_id: str) -> dict | None:
    """Load the login account document by user id (cross-partition)."""
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    for item in items:
        if _is_account_user(item):
            return item
    return None


def _get_role_by_id(role_id: str, tenant_id: str) -> dict | None:
    roles_container = get_container("roles", "/tenant_id")
    items = list(roles_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id", "value": role_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def _get_role_by_name(role_name: str, tenant_id: str) -> dict | None:
    from smart_invoice_pro.api.roles_permissions_api import _get_or_seed_roles

    _get_or_seed_roles(tenant_id)
    roles_container = get_container("roles", "/tenant_id")
    items = list(roles_container.query_items(
        query="SELECT * FROM c WHERE c.name = @name AND c.tenant_id = @tid",
        parameters=[
            {"name": "@name", "value": role_name},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def _resolve_role_doc(user: dict, tenant_id: str) -> dict | None:
    role_id = user.get('role_id')
    if role_id and tenant_id:
        role_doc = _get_role_by_id(role_id, tenant_id)
        if role_doc:
            return role_doc
    role_name = user.get('role', 'Sales')
    if tenant_id and role_name:
        return _get_role_by_name(role_name, tenant_id)
    return None


def is_admin_user(user: dict, tenant_id: str) -> bool:
    """Return True when the user should bypass granular permission checks."""
    if not user:
        return False
    if user.get('is_super_admin'):
        return True
    if user.get('role') in _ADMIN_ROLE_NAMES:
        return True
    role_doc = _resolve_role_doc(user, tenant_id)
    return bool(role_doc and role_doc.get('name') in _ADMIN_ROLE_NAMES)


def resolve_user_permissions(user_id: str, tenant_id: str) -> tuple[bool, dict]:
    """
    Return (is_admin, permissions_dict) for API / UI enforcement.

    is_admin          – bypass all module checks when True
    permissions_dict  – {module: {action: bool}} from the role document
    """
    user = fetch_account_user(user_id)
    if not user:
        return False, {}

    user_tenant = user.get('tenant_id')
    if user_tenant and tenant_id and user_tenant != tenant_id:
        return False, {}

    status = (user.get('status') or '').lower()
    if status == 'suspended' or user.get('is_active') is False:
        return False, {}

    if is_admin_user(user, tenant_id):
        return True, {}

    role_doc = _resolve_role_doc(user, tenant_id)
    if not role_doc:
        return False, {}

    return False, role_doc.get('permissions') or {}

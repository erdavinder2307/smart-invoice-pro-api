"""
memberships.py
==============
Access for users from another organisation (e.g. a CA firm) to a tenant's books.

A membership is granted by a tenant Admin to an email address. It binds to the
Books account with that email, once its email is verified, the first time that user lists or
switches organisations. While it is active, the user can switch into the
tenant and works there with the membership's role, never with the role they
hold in their own organisation.

Document (container "memberships", partition /tenant_id):
{
    "id": "<uuid>", "tenant_id": "<client tenant>", "email": "ca@example.com",
    "user_id": null | "<bound user id>", "role": "Accountant", "role_id": "<role id>",
    "status": "active" | "revoked", "created_by": "<admin user id>",
    "created_at": "...", "revoked_at": "..."
}
"""

from __future__ import annotations

from smart_invoice_pro.utils.cosmos_client import get_container

# External members never get the Admin role: users, roles and billing of a
# tenant stay with its own people.
FORBIDDEN_MEMBER_ROLES = frozenset({"Admin"})


def _container():
    return get_container("memberships", "/tenant_id")


def home_tenant_id(user: dict) -> str | None:
    """The tenant a user signed up in (old accounts use their own id)."""
    if not user:
        return None
    return user.get("tenant_id") or user.get("id")


def normalize_email(value) -> str:
    return (value or "").strip().lower() if isinstance(value, str) else ""


def can_bind(user: dict) -> bool:
    """
    Only an account that has proved it owns its email can claim a membership.

    Sign-up does not check emails (anyone can register with the CA's address),
    and accounts without the flag cannot be told apart from those, so the flag
    must be explicitly True.
    """
    return bool(normalize_email(user.get("email"))) and user.get("email_verified") is True


def get_membership(membership_id: str, tenant_id: str) -> dict | None:
    items = list(_container().query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id", "value": membership_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def is_active_for(membership: dict | None, user_id: str, tenant_id: str) -> bool:
    return bool(
        membership
        and membership.get("status") == "active"
        and membership.get("tenant_id") == tenant_id
        and membership.get("user_id") == user_id
    )


def find_active_membership(tenant_id: str, user_id: str) -> dict | None:
    """The active membership bound to this user in this tenant, if any."""
    if not tenant_id or not user_id:
        return None
    items = list(_container().query_items(
        query=(
            "SELECT * FROM c WHERE c.tenant_id = @tid AND c.user_id = @uid "
            "AND c.status = 'active'"
        ),
        parameters=[
            {"name": "@tid", "value": tenant_id},
            {"name": "@uid", "value": user_id},
        ],
        enable_cross_partition_query=True,
    ))
    return next((m for m in items if is_active_for(m, user_id, tenant_id)), None)


def list_memberships_for_user(user: dict) -> list:
    """Active memberships of this user, binding unclaimed ones that match their email."""
    user_id = user.get("id")
    params = [{"name": "@uid", "value": user_id}]
    query = "SELECT * FROM c WHERE c.status = 'active' AND c.user_id = @uid"
    email = normalize_email(user.get("email"))
    if can_bind(user):
        query = (
            "SELECT * FROM c WHERE c.status = 'active' AND "
            "(c.user_id = @uid OR (c.email = @email AND (NOT IS_DEFINED(c.user_id) OR IS_NULL(c.user_id))))"
        )
        params.append({"name": "@email", "value": email})
    items = list(_container().query_items(
        query=query, parameters=params, enable_cross_partition_query=True,
    ))
    home = home_tenant_id(user)
    result = []
    for item in items:
        if item.get("tenant_id") == home:
            continue
        if not item.get("user_id"):
            item["user_id"] = user_id
            _container().replace_item(item=item["id"], body=item)
        if item.get("user_id") == user_id:
            result.append(item)
    return result


def tenant_role_view(user: dict, tenant_id: str) -> dict | None:
    """
    The user as seen inside `tenant_id` for role checks.

    In their own tenant this is the account itself. In another tenant it is a
    view carrying only the membership's role (no super-admin flag), or None
    when there is no active membership.
    """
    if not user:
        return None
    if not tenant_id or home_tenant_id(user) == tenant_id:
        return user
    membership = find_active_membership(tenant_id, user.get("id"))
    if not membership:
        # Old account documents have no tenant_id; their tenant comes from the
        # token, as before memberships existed.
        return user if not user.get("tenant_id") else None
    return {
        "id": user.get("id"),
        "tenant_id": tenant_id,
        "role": membership.get("role") or "",
        "role_id": membership.get("role_id") or "",
        "status": user.get("status"),
        "is_active": user.get("is_active"),
        "membership_id": membership.get("id"),
    }

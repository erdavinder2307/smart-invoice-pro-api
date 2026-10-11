"""
entitlements.py
===============
Plan limits for Solidev Books tenants: one table of what each plan allows,
and the checks the counted create endpoints run before they write.

At a limit the API answers HTTP 402 with one error shape the app can show:
    {"error": "plan_limit", "code": "seat_limit" | "feature",
     "message": "...", "upgrade_url": "/settings/billing"}

DEMO and INTERNAL tenants and super-admins are never limited. A tenant with no
tenant document (older sign-ups) is not limited either; that is logged.
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import g, jsonify, request

from smart_invoice_pro.utils.tenant_service import get_tenant_by_id

logger = logging.getLogger(__name__)

UPGRADE_URL = "/settings/billing"

# Single source of truth for the API. Prices live with billing, not here.
# seats: active login accounts, not counting one free accountant seat.
PLANS = {
    "trial": {
        "seats": 3,
        "accountant_seats": 1,
        "features": {"approvals", "api_access"},
    },
    "starter": {
        "seats": 3,
        "accountant_seats": 1,
        "features": set(),
    },
    "growth": {
        "seats": 10,
        "accountant_seats": 1,
        "features": {"approvals", "api_access"},
    },
}
# Legacy plan names stay valid on existing tenant documents.
PLAN_ALIASES = {"pro": "growth", "enterprise": "growth"}

EXEMPT_TENANT_TYPES = frozenset({"DEMO", "INTERNAL"})
ACCOUNTANT_ROLE = "accountant"


def resolve_plan(plan: str | None) -> str:
    """Plan key in PLANS for a stored plan name; unknown names fall back to trial."""
    name = (plan or "trial").strip().lower()
    name = PLAN_ALIASES.get(name, name)
    return name if name in PLANS else "trial"


def plan_limit_response(code: str, message: str):
    return jsonify({
        "error": "plan_limit",
        "code": code,
        "message": message,
        "upgrade_url": UPGRADE_URL,
    }), 402


def _request_is_exempt() -> bool:
    if getattr(g, "is_super_admin", False):
        return True
    from smart_invoice_pro.utils.demo_guard import request_is_demo_mode
    return request_is_demo_mode()


def get_tenant_entitlements(tenant_id: str) -> dict | None:
    """The tenant's plan and limits, read once per request. None means not limited."""
    cache = getattr(g, "_entitlements", None)
    if cache is None:
        cache = g._entitlements = {}
    if tenant_id in cache:
        return cache[tenant_id]

    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        logger.warning("entitlements: no tenant document for %s; plan limits not applied", tenant_id)
        result = None
    elif (tenant.get("tenant_type") or "").upper() in EXEMPT_TENANT_TYPES:
        result = None
    else:
        plan = resolve_plan(tenant.get("plan"))
        result = {"plan": plan, **PLANS[plan]}
    cache[tenant_id] = result
    return result


def _is_accountant(user: dict) -> bool:
    return (user.get("role") or "").strip().lower() == ACCOUNTANT_ROLE


def role_change_takes_seat(old_role: str | None, new_role: str | None) -> bool:
    """True when an active user moving from ``old_role`` to ``new_role`` may need a seat it did not use:
    an accountant (possibly the free seat) becoming any other role."""
    def is_acc(role):
        return (role or "").strip().lower() == ACCOUNTANT_ROLE
    return is_acc(old_role) and not is_acc(new_role)


def check_seat_available(tenant_id: str, active_users: list[dict], new_role: str | None):
    """None when one more active user with ``new_role`` fits the plan, else a 402 response.

    ``active_users`` are the tenant's active login accounts, without the user being added.
    One accountant is free; any further accountant takes a normal seat.
    """
    if _request_is_exempt():
        return None
    ent = get_tenant_entitlements(tenant_id)
    if ent is None:
        return None

    accountants = sum(1 for u in active_users if _is_accountant(u))
    free_accountants = min(accountants, ent["accountant_seats"])
    used = len(active_users) - free_accountants
    if (new_role or "").strip().lower() == ACCOUNTANT_ROLE and accountants < ent["accountant_seats"]:
        return None
    if used < ent["seats"]:
        return None
    return plan_limit_response(
        "seat_limit",
        f"Your {ent['plan']} plan includes {ent['seats']} users. "
        "Upgrade your plan or deactivate a user to add another.",
    )


def require_entitlement(feature: str):
    """Decorator: 402 unless the tenant's plan includes ``feature``."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _request_is_exempt():
                ent = get_tenant_entitlements(getattr(request, "tenant_id", None))
                if ent is not None and feature not in ent["features"]:
                    return plan_limit_response(
                        "feature",
                        f"This feature is not included in your {ent['plan']} plan.",
                    )
            return fn(*args, **kwargs)

        return wrapper

    return decorator

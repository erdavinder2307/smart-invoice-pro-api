"""
entitlements.py
===============
Plan limits for Solidev Books tenants: one table of what each plan allows,
and the checks the counted create endpoints run before they write.

At a limit the API answers HTTP 402 with one error shape the app can show:
    {"error": "plan_limit", "code": "seat_limit" | "feature",
     "message": "...", "upgrade_url": "/settings/billing"}

After a trial ends (no active payment), or 7 days after a failed payment's
period end, every write answers 402 with code "trial_ended" or
"payment_failed"; reads keep working and nothing is deleted.

DEMO and INTERNAL tenants and super-admins are never limited. A tenant with no
tenant document (older sign-ups), or a trial with no trial_ends_at yet, is not
limited either; that is logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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

PAYMENT_GRACE = timedelta(days=7)
FAILED_BILLING_STATUSES = frozenset({"halted", "cancelled"})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Writes a locked tenant can still make (login and refresh never reach the check).
# Password change and session revoke stay open: they are security controls, not trial features.
LOCKED_WRITE_PATHS = frozenset({"/api/auth/logout", "/api/me/password"})
LOCKED_WRITE_PREFIXES = ("/api/billing/", "/api/me/sessions/")


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


def _limited_tenant(tenant_id: str) -> dict | None:
    """The tenant document, read once per request. None when it is missing or exempt."""
    cache = getattr(g, "_limited_tenants", None)
    if cache is None:
        cache = g._limited_tenants = {}
    if tenant_id in cache:
        return cache[tenant_id]

    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        logger.warning("entitlements: no tenant document for %s; plan limits not applied", tenant_id)
        tenant = None
    elif (tenant.get("tenant_type") or "").upper() in EXEMPT_TENANT_TYPES:
        tenant = None
    cache[tenant_id] = tenant
    return tenant


def get_tenant_entitlements(tenant_id: str) -> dict | None:
    """The tenant's plan and limits. None means not limited."""
    tenant = _limited_tenant(tenant_id)
    if tenant is None:
        return None
    plan = resolve_plan(tenant.get("plan"))
    return {"plan": plan, **PLANS[plan]}


def _parse_utc(value) -> datetime | None:
    """Naive UTC datetime from a stored ISO timestamp (with or without an offset), else None."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def account_lock_code(tenant: dict, now: datetime) -> str | None:
    """"trial_ended" or "payment_failed" when the tenant may no longer write at ``now`` (naive UTC), else None."""
    billing = tenant.get("billing") or {}
    billing_status = (billing.get("status") or "").strip().lower()

    if (tenant.get("plan") or "trial").strip().lower() == "trial":
        if billing_status == "active":
            return None
        ends = _parse_utc(tenant.get("trial_ends_at"))
        if ends is None:
            logger.warning("entitlements: trial tenant %s has no trial_ends_at; not locked", tenant.get("id"))
            return None
        return "trial_ended" if now > ends else None

    if billing_status in FAILED_BILLING_STATUSES:
        period_end = _parse_utc(tenant.get("plan_period_end"))
        if period_end is not None and now > period_end + PAYMENT_GRACE:
            return "payment_failed"
    return None


_LOCK_MESSAGES = {
    "trial_ended": "Your free trial has ended. Choose a plan to keep adding and changing data. "
                   "Everything you entered is still here.",
    "payment_failed": "Your last payment did not go through. Update your payment to keep adding and "
                      "changing data. Everything you entered is still here.",
}


def enforce_account_writes():
    """before_request check, after authentication: 402 for a write by a tenant whose trial or payment lapsed."""
    if request.method in READ_METHODS:
        return None
    path = request.path
    if path in LOCKED_WRITE_PATHS or path.startswith(LOCKED_WRITE_PREFIXES):
        return None
    if _request_is_exempt():
        return None
    tenant = _limited_tenant(getattr(request, "tenant_id", None))
    if tenant is None:
        return None
    code = account_lock_code(tenant, datetime.utcnow())
    if code is None:
        return None
    return plan_limit_response(code, _LOCK_MESSAGES[code])


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

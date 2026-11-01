"""
Entitlement checker — middleware decorator for subscription enforcement.

Usage on any mutation endpoint:
    @check_entitlement("invoices")
    def create_invoice():
        ...
"""
import logging
from functools import wraps

from flask import jsonify, g, request

logger = logging.getLogger(__name__)

_METRIC_MAP = {
    "invoices": ("invoices_created", "can_create_invoice"),
    "quotes": ("quotes_created", "can_create_quote"),
    "purchase_orders": ("purchase_orders_created", "can_create_po"),
    "bills": ("bills_created", "can_create_bill"),
    "expenses": ("expenses_created", "can_create_expense"),
    "users": ("users_active", "can_add_user"),
}


def _get_tenant_id() -> str:
    return getattr(g, "tenant_id", None) or getattr(request, "tenant_id", None)


def check_entitlement(resource: str):
    """
    Decorator that checks subscription status and resource limits before
    allowing a mutation. Returns 402 if subscription is expired/suspended,
    429 if the plan limit for that resource is reached.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            tenant_id = _get_tenant_id()
            if not tenant_id:
                return f(*args, **kwargs)

            try:
                from smart_invoice_pro.services.subscription_service import (
                    get_entitlements,
                    increment_usage,
                )
                entitlements = get_entitlements(tenant_id)

                if entitlements.get("read_only") or entitlements.get("upgrade_required"):
                    return jsonify({
                        "error": "subscription_expired",
                        "message": "Your trial or subscription has ended. Please upgrade to continue.",
                        "upgrade_url": "/settings/billing/upgrade",
                        "status": entitlements.get("status"),
                    }), 402

                _, entitlement_key = _METRIC_MAP.get(resource, (None, None))
                if entitlement_key and not entitlements.get(entitlement_key, True):
                    metric_key, _ = _METRIC_MAP[resource]
                    limit = entitlements.get("limits", {}).get(f"{resource}_per_month") or \
                            entitlements.get("limits", {}).get("max_users")
                    return jsonify({
                        "error": "limit_reached",
                        "resource": resource,
                        "message": f"You have reached your plan limit for {resource}.",
                        "limit": limit,
                        "upgrade_url": "/settings/billing/upgrade",
                    }), 429

                # Run the actual endpoint
                result = f(*args, **kwargs)

                # Increment usage on success (2xx response)
                try:
                    status_code = result[1] if isinstance(result, tuple) else 200
                    metric_key, _ = _METRIC_MAP.get(resource, (None, None))
                    if metric_key and status_code in (200, 201):
                        increment_usage(tenant_id, metric_key)
                except Exception:
                    pass

                return result

            except Exception as e:
                logger.error(f"[entitlement_checker] Error checking entitlement for {resource}: {e}")
                # On error, allow through — don't block legitimate users on middleware failures
                return f(*args, **kwargs)

        return wrapper
    return decorator

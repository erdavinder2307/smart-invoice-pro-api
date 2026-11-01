"""
Subscription service — core business logic for trial and subscription management.
All mutation functions emit a subscription_event for auditability.
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone

from smart_invoice_pro.utils.cosmos_client import (
    subscriptions_container,
    subscription_events_container,
    usage_records_container,
    plans_container,
    tenants_container,
    trial_extension_requests_container,
)

logger = logging.getLogger(__name__)

TRIAL_DAYS = 30
EXTENSION_1_DAYS = 30
EXTENSION_2_DAYS = 30
FOUNDING_MAX_DAYS = 180

TRIAL_STATUSES = {
    "trial",
    "trial_extension_1",
    "trial_extension_2",
    "founding_customer",
}

ACTIVE_STATUSES = TRIAL_STATUSES | {"active"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_from_now(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _emit_event(tenant_id: str, event_type: str, data: dict):
    try:
        subscription_events_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "type": event_type,
            "data": data,
            "timestamp": _now_iso(),
        })
    except Exception as e:
        logger.error(f"[subscription_service] Failed to emit event {event_type}: {e}")


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_subscription(tenant_id: str) -> dict | None:
    """Return the active subscription doc for a tenant, or None."""
    try:
        items = list(subscriptions_container.query_items(
            query="SELECT * FROM c WHERE c.tenant_id = @tid ORDER BY c.created_at DESC OFFSET 0 LIMIT 1",
            parameters=[{"name": "@tid", "value": tenant_id}],
            partition_key=tenant_id,
        ))
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[subscription_service] get_subscription failed for {tenant_id}: {e}")
        return None


def get_plan(plan_id: str) -> dict | None:
    """Return a plan doc by id."""
    try:
        items = list(plans_container.query_items(
            query="SELECT * FROM c WHERE c.id = @pid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@pid", "value": plan_id}],
            enable_cross_partition_query=True,
        ))
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[subscription_service] get_plan failed for {plan_id}: {e}")
        return None


def get_public_plans() -> list:
    """Return all active, public plans sorted by sort_order."""
    try:
        items = list(plans_container.query_items(
            query="SELECT * FROM c WHERE c.is_public = true AND c.is_active = true ORDER BY c.sort_order ASC",
            enable_cross_partition_query=True,
        ))
        return items
    except Exception as e:
        logger.error(f"[subscription_service] get_public_plans failed: {e}")
        return []


def get_current_usage(tenant_id: str) -> dict:
    """Return usage record for the current billing period, creating it if absent."""
    period_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_key = f"usage_{tenant_id}_{period_start.year}_{period_start.month:02d}"

    try:
        items = list(usage_records_container.query_items(
            query="SELECT * FROM c WHERE c.id = @pid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@pid", "value": period_key}],
            partition_key=tenant_id,
        ))
        if items:
            return items[0]
    except Exception:
        pass

    # Bootstrap a new usage record for this period
    record = {
        "id": period_key,
        "tenant_id": tenant_id,
        "billing_period_start": period_start.isoformat(),
        "billing_period_end": (period_start.replace(month=period_start.month % 12 + 1) if period_start.month < 12
                               else period_start.replace(year=period_start.year + 1, month=1)).isoformat(),
        "metrics": {
            "invoices_created": 0,
            "quotes_created": 0,
            "purchase_orders_created": 0,
            "bills_created": 0,
            "expenses_created": 0,
            "users_active": 0,
            "api_calls": 0,
        },
        "warnings_sent": {
            "invoices_80pct": False,
            "invoices_95pct": False,
            "users_limit": False,
        },
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        usage_records_container.create_item(body=record)
    except Exception:
        pass
    return record


def increment_usage(tenant_id: str, metric: str, amount: int = 1):
    """Atomically increment a usage metric for the current period."""
    record = get_current_usage(tenant_id)
    record["metrics"][metric] = record["metrics"].get(metric, 0) + amount
    record["updated_at"] = _now_iso()
    try:
        usage_records_container.replace_item(item=record["id"], body=record)
    except Exception as e:
        logger.error(f"[subscription_service] increment_usage failed: {e}")


def get_entitlements(tenant_id: str) -> dict:
    """
    Compute what the tenant is currently allowed to do.
    Returns a dict of booleans and current limits.
    """
    sub = get_subscription(tenant_id)
    if not sub:
        return {"status": "no_subscription", "can_create": False}

    status = sub.get("status", "expired")

    if status not in ACTIVE_STATUSES:
        return {
            "status": status,
            "can_create": False,
            "read_only": True,
            "upgrade_required": True,
        }

    plan = get_plan(sub.get("plan_id", "starter_monthly")) or {}
    features = plan.get("features", {})
    usage = get_current_usage(tenant_id)
    metrics = usage.get("metrics", {})

    def _within_limit(metric_key: str, limit_key: str) -> bool:
        limit = features.get(limit_key, -1)
        if limit == -1:
            return True
        return metrics.get(metric_key, 0) < limit

    trial_ends_at = sub.get("trial_ends_at")
    days_remaining = None
    if trial_ends_at and status in TRIAL_STATUSES:
        try:
            end = datetime.fromisoformat(trial_ends_at.replace("Z", "+00:00"))
            days_remaining = max(0, (end - datetime.now(timezone.utc)).days)
        except Exception:
            pass

    return {
        "status": status,
        "plan_id": sub.get("plan_id"),
        "plan_name": plan.get("name", "Trial"),
        "days_remaining": days_remaining,
        "trial_ends_at": trial_ends_at,
        "can_create": True,
        "read_only": False,
        "upgrade_required": False,
        "can_create_invoice": _within_limit("invoices_created", "invoices_per_month"),
        "can_create_quote": _within_limit("quotes_created", "quotes_per_month"),
        "can_create_po": _within_limit("purchase_orders_created", "purchase_orders_per_month"),
        "can_create_bill": _within_limit("bills_created", "bills_per_month"),
        "can_create_expense": _within_limit("expenses_created", "expenses_per_month"),
        "can_add_user": _within_limit("users_active", "max_users"),
        "api_access": features.get("api_access", False),
        "advanced_reports": features.get("advanced_reports", False),
        "automation": features.get("automation_rules", 0) > 0,
        "limits": {
            "invoices_per_month": features.get("invoices_per_month", -1),
            "max_users": features.get("max_users", -1),
        },
        "usage": {
            "invoices_created": metrics.get("invoices_created", 0),
            "users_active": metrics.get("users_active", 0),
        },
    }


# ── Mutation helpers ──────────────────────────────────────────────────────────

def create_trial_subscription(tenant_id: str, plan_id: str = "starter_monthly") -> dict:
    """
    Called during registration. Creates a 30-day trial subscription and
    ensures the tenant doc reflects plan=trial.
    """
    trial_ends_at = _days_from_now(TRIAL_DAYS)
    now = _now_iso()

    sub = {
        "id": f"sub_{tenant_id}",
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "status": "trial",
        "billing_cycle": "monthly",
        "current_period_start": now,
        "current_period_end": trial_ends_at,
        "trial_ends_at": trial_ends_at,
        "trial_days_granted": TRIAL_DAYS,
        "trial_extended_by_days": 0,
        "trial_extension_1_requested_at": None,
        "trial_extension_2_requested_at": None,
        "trial_extension_2_approved_by": None,
        "trial_extension_2_approved_at": None,
        "is_founding_customer": False,
        "founding_customer_granted_by": None,
        "founding_customer_granted_at": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "payment_method_id": None,
        "payment_gateway": None,
        "gateway_subscription_id": None,
        "auto_renew": False,
        "next_billing_date": None,
        "amount_inr": None,
        "seats": 3,
        "created_at": now,
        "updated_at": now,
    }

    try:
        subscriptions_container.create_item(body=sub)
    except Exception:
        # If already exists (re-registration edge case), return existing
        existing = get_subscription(tenant_id)
        return existing or sub

    _emit_event(tenant_id, "subscription.trial_started", {
        "plan_id": plan_id,
        "trial_ends_at": trial_ends_at,
    })

    # Sync tenant plan field
    _sync_tenant_plan(tenant_id, "trial")

    return sub


def _sync_tenant_plan(tenant_id: str, plan_slug: str):
    """Keep tenants_container.plan in sync with subscription status."""
    try:
        items = list(tenants_container.query_items(
            query="SELECT * FROM c WHERE c.id = @tid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        if items:
            doc = items[0]
            doc["plan"] = plan_slug
            doc["updated_at"] = _now_iso()
            tenants_container.replace_item(item=doc["id"], body=doc)
    except Exception as e:
        logger.error(f"[subscription_service] _sync_tenant_plan failed: {e}")


def activate_subscription(tenant_id: str, plan_id: str, billing_cycle: str,
                           amount_inr: float, gateway: str = "zoho_payments",
                           gateway_sub_id: str = None) -> dict:
    """Activate a paid subscription after successful payment."""
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError(f"No subscription found for tenant {tenant_id}")

    now = _now_iso()
    days = 30 if billing_cycle == "monthly" else 365
    period_end = _days_from_now(days)

    sub["status"] = "active"
    sub["plan_id"] = plan_id
    sub["billing_cycle"] = billing_cycle
    sub["current_period_start"] = now
    sub["current_period_end"] = period_end
    sub["next_billing_date"] = period_end
    sub["amount_inr"] = amount_inr
    sub["payment_gateway"] = gateway
    sub["gateway_subscription_id"] = gateway_sub_id
    sub["auto_renew"] = True
    sub["updated_at"] = now

    subscriptions_container.replace_item(item=sub["id"], body=sub)
    _emit_event(tenant_id, "subscription.activated", {
        "plan_id": plan_id,
        "billing_cycle": billing_cycle,
        "amount_inr": amount_inr,
    })

    plan = get_plan(plan_id)
    slug = plan.get("slug", "starter") if plan else "starter"
    _sync_tenant_plan(tenant_id, slug)
    return sub


def request_extension(tenant_id: str, extension_type: str, reason: str = "") -> dict:
    """
    extension_type: "1" | "2"
    Extension 1 auto-approves. Extension 2 queues for admin approval.
    """
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError("No subscription found")

    if sub["status"] not in TRIAL_STATUSES:
        raise ValueError("Extension only valid for trial accounts")

    now = _now_iso()

    if extension_type == "1":
        if sub.get("trial_extension_1_requested_at"):
            raise ValueError("Extension 1 already granted")

        new_end = (
            datetime.fromisoformat(sub["trial_ends_at"].replace("Z", "+00:00"))
            + timedelta(days=EXTENSION_1_DAYS)
        ).isoformat()

        sub["trial_ends_at"] = new_end
        sub["current_period_end"] = new_end
        sub["trial_extended_by_days"] = sub.get("trial_extended_by_days", 0) + EXTENSION_1_DAYS
        sub["trial_extension_1_requested_at"] = now
        sub["status"] = "trial_extension_1"
        sub["updated_at"] = now
        subscriptions_container.replace_item(item=sub["id"], body=sub)
        _emit_event(tenant_id, "subscription.extension_1_granted", {"new_trial_ends_at": new_end})
        return {"status": "granted", "new_trial_ends_at": new_end, "extension_type": "1"}

    elif extension_type == "2":
        if sub.get("trial_extension_2_requested_at"):
            raise ValueError("Extension 2 already requested")
        if not sub.get("trial_extension_1_requested_at"):
            raise ValueError("Extension 1 must be used before requesting Extension 2")

        sub["trial_extension_2_requested_at"] = now
        sub["updated_at"] = now
        subscriptions_container.replace_item(item=sub["id"], body=sub)

        # Create a pending request for admin approval
        trial_extension_requests_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "extension_type": "2",
            "reason": reason,
            "status": "pending",
            "requested_at": now,
            "reviewed_at": None,
            "reviewed_by": None,
            "admin_note": None,
        })
        _emit_event(tenant_id, "subscription.extension_2_requested", {"reason": reason})
        return {"status": "pending_approval", "extension_type": "2"}

    raise ValueError("extension_type must be '1' or '2'")


def approve_extension_2(tenant_id: str, request_id: str, admin_user_id: str,
                         approved: bool, admin_note: str = "") -> dict:
    """Admin approves or denies an Extension 2 request."""
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError("No subscription found")

    now = _now_iso()

    # Update the request doc
    try:
        items = list(trial_extension_requests_container.query_items(
            query="SELECT * FROM c WHERE c.id = @rid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@rid", "value": request_id}],
            partition_key=tenant_id,
        ))
        if items:
            req = items[0]
            req["status"] = "approved" if approved else "denied"
            req["reviewed_at"] = now
            req["reviewed_by"] = admin_user_id
            req["admin_note"] = admin_note
            trial_extension_requests_container.replace_item(item=req["id"], body=req)
    except Exception as e:
        logger.error(f"[subscription_service] approve_extension_2 request update failed: {e}")

    if approved:
        new_end = (
            datetime.fromisoformat(sub["trial_ends_at"].replace("Z", "+00:00"))
            + timedelta(days=EXTENSION_2_DAYS)
        ).isoformat()

        sub["trial_ends_at"] = new_end
        sub["current_period_end"] = new_end
        sub["trial_extended_by_days"] = sub.get("trial_extended_by_days", 0) + EXTENSION_2_DAYS
        sub["trial_extension_2_approved_by"] = admin_user_id
        sub["trial_extension_2_approved_at"] = now
        sub["status"] = "trial_extension_2"
        sub["updated_at"] = now
        subscriptions_container.replace_item(item=sub["id"], body=sub)
        _emit_event(tenant_id, "subscription.extension_2_approved", {
            "new_trial_ends_at": new_end,
            "approved_by": admin_user_id,
        })
        return {"status": "approved", "new_trial_ends_at": new_end}

    _emit_event(tenant_id, "subscription.extension_2_denied", {"admin_note": admin_note})
    return {"status": "denied", "admin_note": admin_note}


def grant_founding_customer(tenant_id: str, admin_user_id: str, notes: str = "") -> dict:
    """Grant Founding Customer status — extends trial to 180 days total from signup."""
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError("No subscription found")

    now = _now_iso()
    signup_date = datetime.fromisoformat(sub["created_at"].replace("Z", "+00:00"))
    new_end = (signup_date + timedelta(days=FOUNDING_MAX_DAYS)).isoformat()

    sub["is_founding_customer"] = True
    sub["founding_customer_granted_by"] = admin_user_id
    sub["founding_customer_granted_at"] = now
    sub["status"] = "founding_customer"
    sub["trial_ends_at"] = new_end
    sub["current_period_end"] = new_end
    sub["trial_extended_by_days"] = FOUNDING_MAX_DAYS - TRIAL_DAYS
    sub["updated_at"] = now
    subscriptions_container.replace_item(item=sub["id"], body=sub)

    _emit_event(tenant_id, "subscription.founding_customer_granted", {
        "granted_by": admin_user_id,
        "new_trial_ends_at": new_end,
        "notes": notes,
    })
    return sub


def expire_trial(tenant_id: str) -> dict:
    """Called by the daily cron when a trial_ends_at passes."""
    sub = get_subscription(tenant_id)
    if not sub:
        return {}

    sub["status"] = "expired"
    sub["updated_at"] = _now_iso()
    subscriptions_container.replace_item(item=sub["id"], body=sub)

    _emit_event(tenant_id, "subscription.trial_expired", {})
    _sync_tenant_plan(tenant_id, "trial")

    # Soft-suspend the tenant
    try:
        items = list(tenants_container.query_items(
            query="SELECT * FROM c WHERE c.id = @tid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        if items:
            doc = items[0]
            doc["status"] = "suspended"
            doc["updated_at"] = _now_iso()
            tenants_container.replace_item(item=doc["id"], body=doc)
    except Exception as e:
        logger.error(f"[subscription_service] expire_trial tenant suspend failed: {e}")

    return sub


def cancel_subscription(tenant_id: str, reason: str = "") -> dict:
    """Soft-cancel: begin 7-day grace period, data retained."""
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError("No subscription found")

    now = _now_iso()
    sub["status"] = "cancelled"
    sub["cancelled_at"] = now
    sub["cancel_reason"] = reason
    sub["auto_renew"] = False
    sub["updated_at"] = now
    subscriptions_container.replace_item(item=sub["id"], body=sub)

    _emit_event(tenant_id, "subscription.cancelled", {"reason": reason})
    return sub


def grant_admin_extension(tenant_id: str, days: int, admin_user_id: str, reason: str = "") -> dict:
    """Super-admin manually grants any number of extra trial days."""
    sub = get_subscription(tenant_id)
    if not sub:
        raise ValueError("No subscription found")

    now = _now_iso()
    current_end_str = sub.get("trial_ends_at") or sub.get("current_period_end") or now
    try:
        current_end = datetime.fromisoformat(current_end_str.replace("Z", "+00:00"))
    except Exception:
        current_end = datetime.now(timezone.utc)

    new_end = (current_end + timedelta(days=days)).isoformat()
    sub["trial_ends_at"] = new_end
    sub["current_period_end"] = new_end
    sub["trial_extended_by_days"] = sub.get("trial_extended_by_days", 0) + days
    sub["status"] = sub["status"] if sub["status"] in TRIAL_STATUSES else "trial"
    sub["updated_at"] = now
    subscriptions_container.replace_item(item=sub["id"], body=sub)

    _emit_event(tenant_id, "subscription.admin_extension_granted", {
        "days": days,
        "new_trial_ends_at": new_end,
        "granted_by": admin_user_id,
        "reason": reason,
    })
    return sub

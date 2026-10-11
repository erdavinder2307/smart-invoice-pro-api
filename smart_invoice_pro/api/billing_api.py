"""
billing_api.py
==============
Paid plans for Solidev Books through Razorpay Subscriptions.

  GET  /api/billing/plans                  public: plans, prices and whether checkout is on
  POST /api/billing/subscriptions          Admin: start a subscription, returns what Checkout needs
  GET  /api/billing/subscription           Admin: current plan, period end and billing status
  POST /api/billing/subscriptions/cancel   Admin: cancel at the end of the paid period
  POST /api/billing/webhook                public: Razorpay events, signature-checked
  GET  /api/billing/invoices               Admin: the tenant's GST tax invoices
  GET  /api/billing/invoices/<id>/pdf      Admin: one tax invoice as a PDF

The tenant's plan changes only through the signed webhook, never through the
browser's success callback. Every captured charge gets a GST tax invoice
(utils/tax_invoices.py); checkout stays off until the seller settings it needs are set.

Configuration (names only; values live in the app settings):
  BILLING_ENABLED             "true" to turn the paid endpoints on (default off → 503)
  RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
  RAZORPAY_PLAN_STARTER_MONTHLY, RAZORPAY_PLAN_STARTER_YEARLY,
  RAZORPAY_PLAN_GROWTH_MONTHLY, RAZORPAY_PLAN_GROWTH_YEARLY
  SELLER_* and BILLING_SAC_CODE for tax invoices (see utils/tax_invoices.py)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

import requests
from azure.cosmos import exceptions
from flask import Blueprint, jsonify, make_response, request

from smart_invoice_pro.api.invoice_generation import build_tax_invoice_pdf
from smart_invoice_pro.api.roles_api import _fetch_user, require_role
from smart_invoice_pro.utils.cosmos_client import (
    billing_events_container,
    settings_container,
    subscriptions_container,
    tax_invoices_container,
    tenants_container,
)
from smart_invoice_pro.utils import tax_invoices
from smart_invoice_pro.utils.entitlements import PLANS
from smart_invoice_pro.utils.tenant_service import get_tenant_by_id

logger = logging.getLogger(__name__)

billing_blueprint = Blueprint("billing", __name__)

RAZORPAY_API = "https://api.razorpay.com/v1"
RAZORPAY_TIMEOUT = 10
GST_RATE = 0.18

# Prices in ₹, before 18% GST. A period without a price is not sold.
PRICES = {
    "starter": {"monthly": 599, "yearly": 5999},
    "growth": {"monthly": 1499},
}
PERIODS = ("monthly", "yearly")
# Billing cycles per subscription: a year of monthly charges, or one yearly charge.
TOTAL_COUNT = {"monthly": 12, "yearly": 1}

ACTIVE_EVENTS = frozenset({"subscription.activated", "subscription.charged"})
STATUS_EVENTS = {
    "subscription.halted": "halted",
    "subscription.pending": "pending",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "cancelled",
}


# ── Config ────────────────────────────────────────────────────────────────────
def _billing_enabled() -> bool:
    return os.getenv("BILLING_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _plan_env_name(plan_code: str, period: str) -> str:
    return f"RAZORPAY_PLAN_{plan_code.upper()}_{period.upper()}"


def _razorpay_plan_id(plan_code: str, period: str) -> str | None:
    return (os.getenv(_plan_env_name(plan_code, period)) or "").strip() or None


def _plan_for_razorpay_id(plan_id: str | None) -> tuple[str, str] | None:
    """(plan_code, period) for a Razorpay plan id from config, else None."""
    if not plan_id:
        return None
    for plan_code, periods in PRICES.items():
        for period in periods:
            if _razorpay_plan_id(plan_code, period) == plan_id:
                return plan_code, period
    return None


def _keys() -> tuple[str, str] | None:
    key_id = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    key_secret = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
    return (key_id, key_secret) if key_id and key_secret else None


def _checkout_enabled() -> bool:
    # No sale without the details a GST tax invoice needs.
    return _billing_enabled() and _keys() is not None and not tax_invoices.missing_config()


def _not_enabled():
    return jsonify({"error": "billing not enabled"}), 503


# ── Razorpay ──────────────────────────────────────────────────────────────────
class RazorpayError(Exception):
    pass


def _razorpay_post(path: str, body: dict) -> dict:
    try:
        resp = requests.post(f"{RAZORPAY_API}{path}", json=body, auth=_keys(), timeout=RAZORPAY_TIMEOUT)
    except requests.RequestException as exc:
        raise RazorpayError(type(exc).__name__) from exc
    if resp.status_code >= 400:
        raise RazorpayError(f"HTTP {resp.status_code}")
    return resp.json()


# ── Tenant helpers ────────────────────────────────────────────────────────────
def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _iso_from_unix(seconds) -> str | None:
    """Naive-UTC ISO timestamp (the format tenant documents use) from Razorpay's unix seconds."""
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _save_tenant(tenant: dict) -> None:
    tenant["updated_at"] = _utc_now_iso()
    tenants_container.replace_item(item=tenant["id"], body=tenant)


# ── GET /billing/plans ────────────────────────────────────────────────────────
@billing_blueprint.route("/billing/plans", methods=["GET"])
def get_plans():
    """Plans with prices before GST and with GST, and whether checkout is on."""
    plans = []
    for plan_code, periods in PRICES.items():
        limits = PLANS[plan_code]
        plans.append({
            "code": plan_code,
            "seats": limits["seats"],
            "accountant_seats": limits["accountant_seats"],
            "features": sorted(limits["features"]),
            "prices": {
                period: {"amount": amount, "amount_with_gst": round(amount * (1 + GST_RATE))}
                for period, amount in periods.items()
            },
        })
    return jsonify({
        "currency": "INR",
        "gst_rate": GST_RATE,
        "checkout_enabled": _checkout_enabled(),
        "plans": plans,
    }), 200


# ── POST /billing/subscriptions ───────────────────────────────────────────────
@billing_blueprint.route("/billing/subscriptions", methods=["POST"])
@require_role("Admin")
def create_subscription():
    """Create a Razorpay subscription for the signed-in tenant; the browser opens Checkout with it."""
    if not _checkout_enabled():
        return _not_enabled()

    tenant_id = getattr(request, "tenant_id", None)
    data = request.get_json(silent=True) or {}
    plan_code = str(data.get("plan_code") or "").strip().lower()
    period = str(data.get("period") or "").strip().lower()
    if plan_code not in PRICES or period not in PERIODS:
        return jsonify({"error": "Choose plan_code starter or growth and period monthly or yearly"}), 400
    plan_id = _razorpay_plan_id(plan_code, period) if period in PRICES[plan_code] else None
    if not plan_id:
        return jsonify({"error": "This plan is not available for that period"}), 400

    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return jsonify({"error": "Organization not found"}), 404
    billing = dict(tenant.get("billing") or {})
    if billing.get("status") == "active":
        return jsonify({"error": "This organization already has an active subscription"}), 409

    user = _fetch_user(getattr(request, "user_id", None)) or {}
    try:
        if not billing.get("customer_id"):
            customer = _razorpay_post("/customers", {
                "name": tenant.get("name") or "Solidev Books customer",
                "email": user.get("email") or "",
                "fail_existing": "0",
                "notes": {"tenant_id": tenant_id},
            })
            billing["customer_id"] = customer["id"]
            billing["provider"] = "razorpay"
            tenant["billing"] = billing
            _save_tenant(tenant)

        subscription = _razorpay_post("/subscriptions", {
            "plan_id": plan_id,
            "customer_id": billing["customer_id"],
            "total_count": TOTAL_COUNT[period],
            "customer_notify": 1,
            "notes": {"tenant_id": tenant_id, "plan_code": plan_code, "period": period},
        })
    except (RazorpayError, KeyError) as exc:
        logger.error("billing: Razorpay request failed for tenant %s: %s", tenant_id, exc)
        return jsonify({"error": "Could not reach the payment provider. Please try again."}), 502

    now = _utc_now_iso()
    subscriptions_container.upsert_item(body={
        "id": subscription["id"],
        "tenant_id": tenant_id,
        "plan_code": plan_code,
        "period": period,
        "status": subscription.get("status") or "created",
        "created_by": getattr(request, "user_id", None),
        "created_at": now,
        "updated_at": now,
    })
    logger.info("billing: subscription %s created for tenant %s (%s %s)", subscription["id"], tenant_id, plan_code, period)

    return jsonify({
        "subscription_id": subscription["id"],
        "key_id": _keys()[0],
        "plan_code": plan_code,
        "period": period,
        "prefill": {"email": user.get("email") or "", "name": user.get("username") or ""},
    }), 201


# ── GET /billing/subscription ─────────────────────────────────────────────────
@billing_blueprint.route("/billing/subscription", methods=["GET"])
@require_role("Admin")
def get_subscription():
    """The tenant's plan, trial or paid period end, and billing status for the billing page."""
    tenant = get_tenant_by_id(getattr(request, "tenant_id", None))
    if not tenant:
        return jsonify({"error": "Organization not found"}), 404
    billing = tenant.get("billing") or {}
    return jsonify({
        "plan": tenant.get("plan") or "trial",
        "trial_ends_at": tenant.get("trial_ends_at"),
        "plan_period_end": tenant.get("plan_period_end"),
        "billing": {
            "status": billing.get("status"),
            "plan_code": billing.get("plan_code"),
            "period": billing.get("period"),
            "subscription_id": billing.get("subscription_id"),
            "cancel_at_period_end": bool(billing.get("cancel_at_period_end")),
        },
        "checkout_enabled": _checkout_enabled(),
        "invoices": tax_invoices.list_for_tenant(tenant["id"]),
    }), 200


# ── POST /billing/subscriptions/cancel ────────────────────────────────────────
@billing_blueprint.route("/billing/subscriptions/cancel", methods=["POST"])
@require_role("Admin")
def cancel_subscription():
    """Cancel at the end of the paid period; the plan keeps working until plan_period_end."""
    if not _checkout_enabled():
        return _not_enabled()
    tenant = get_tenant_by_id(getattr(request, "tenant_id", None))
    if not tenant:
        return jsonify({"error": "Organization not found"}), 404
    billing = dict(tenant.get("billing") or {})
    subscription_id = billing.get("subscription_id")
    if not subscription_id or billing.get("status") == "cancelled":
        return jsonify({"error": "No subscription to cancel"}), 409

    try:
        _razorpay_post(f"/subscriptions/{subscription_id}/cancel", {"cancel_at_cycle_end": 1})
    except RazorpayError as exc:
        logger.error("billing: cancel failed for tenant %s: %s", tenant["id"], exc)
        return jsonify({"error": "Could not reach the payment provider. Please try again."}), 502

    billing["cancel_at_period_end"] = True
    tenant["billing"] = billing
    _save_tenant(tenant)
    logger.info("billing: tenant %s cancelled subscription %s at period end", tenant["id"], subscription_id)
    return jsonify({"cancel_at_period_end": True, "plan_period_end": tenant.get("plan_period_end")}), 200


# ── POST /billing/webhook ─────────────────────────────────────────────────────
def _tenant_id_for(subscription: dict) -> str | None:
    """The tenant we stored for this subscription id; the notes we sent are the fallback."""
    try:
        stored = list(subscriptions_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": subscription.get("id")}],
            enable_cross_partition_query=True,
        ))
    except exceptions.CosmosHttpResponseError:
        stored = []
    if stored:
        return stored[0].get("tenant_id")
    return (subscription.get("notes") or {}).get("tenant_id")


def _organization_profile(tenant_id: str) -> dict | None:
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[{"name": "@id", "value": f"{tenant_id}:organization_profile"},
                    {"name": "@tid", "value": tenant_id}],
        partition_key=tenant_id,
    ))
    return items[0] if items else None


def _issue_tax_invoice(tenant: dict, subscription: dict, payment: dict, plan_code: str, period: str) -> None:
    """A tax invoice for a captured charge. Storage errors propagate so Razorpay retries the event."""
    if payment.get("status") != "captured" or not payment.get("id") or not payment.get("amount"):
        return
    tax_invoices.create_for_payment(tenant, _organization_profile(tenant["id"]), subscription, payment,
                                    plan_code, period)


def _apply_event(event: str, subscription: dict, payment: dict | None = None) -> None:
    """Update the tenant for one verified subscription event. Data problems are logged, not raised."""
    sub_id = subscription.get("id")
    tenant_id = _tenant_id_for(subscription)
    tenant = get_tenant_by_id(tenant_id) if tenant_id else None
    if not tenant:
        logger.error("billing webhook: %s for subscription %s has no known tenant", event, sub_id)
        return

    billing = dict(tenant.get("billing") or {})
    if event in ACTIVE_EVENTS:
        plan = _plan_for_razorpay_id(subscription.get("plan_id"))
        if plan is None:
            logger.error("billing webhook: %s for tenant %s has an unknown plan id", event, tenant_id)
            return
        plan_code, period = plan
        period_end = _iso_from_unix(subscription.get("current_end"))
        billing.update({
            "provider": "razorpay",
            "subscription_id": sub_id,
            "plan_code": plan_code,
            "period": period,
            "status": "active",
        })
        if subscription.get("customer_id"):
            billing["customer_id"] = subscription["customer_id"]
        tenant["plan"] = plan_code
        tenant["status"] = "active"
        if period_end:
            tenant["plan_period_end"] = period_end
    else:
        if billing.get("subscription_id") not in (None, sub_id):
            logger.warning("billing webhook: %s for old subscription %s of tenant %s ignored", event, sub_id, tenant_id)
            return
        billing["status"] = STATUS_EVENTS[event]

    tenant["billing"] = billing
    _save_tenant(tenant)

    try:
        stored = subscriptions_container.read_item(item=sub_id, partition_key=tenant_id)
        stored.update({"status": subscription.get("status") or billing["status"], "updated_at": _utc_now_iso()})
        subscriptions_container.replace_item(item=sub_id, body=stored)
    except exceptions.CosmosResourceNotFoundError:
        pass

    if event in ACTIVE_EVENTS and payment:
        _issue_tax_invoice(tenant, subscription, payment, billing["plan_code"], billing["period"])
    logger.info("billing webhook: %s applied to tenant %s (subscription %s)", event, tenant_id, sub_id)


@billing_blueprint.route("/billing/webhook", methods=["POST"])
def razorpay_webhook():
    """Razorpay subscription events. No login; the signature over the raw body proves the sender."""
    secret = (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        logger.error("billing webhook: RAZORPAY_WEBHOOK_SECRET is not set; event refused")
        return _not_enabled()

    raw = request.get_data(cache=True)
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        logger.warning("billing webhook: bad signature")
        return jsonify({"error": "Invalid signature"}), 400

    try:
        payload = json.loads(raw)
    except ValueError:
        return jsonify({"error": "Invalid JSON"}), 400
    event = payload.get("event") or ""
    event_id = request.headers.get("X-Razorpay-Event-Id", "").strip()
    if not event_id:
        return jsonify({"error": "Missing event id"}), 400

    try:
        billing_events_container.create_item(body={
            "id": event_id,
            "event_id": event_id,
            "event": event,
            "received_at": _utc_now_iso(),
        })
    except exceptions.CosmosResourceExistsError:
        return jsonify({"status": "already processed"}), 200

    subscription = ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}
    payment = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or None
    if event not in ACTIVE_EVENTS and event not in STATUS_EVENTS:
        logger.info("billing webhook: event %s ignored", event)
        return jsonify({"status": "ignored"}), 200
    if not subscription.get("id"):
        logger.error("billing webhook: %s without a subscription entity", event)
        return jsonify({"status": "ignored"}), 200

    try:
        _apply_event(event, subscription, payment)
    except (exceptions.CosmosHttpResponseError, tax_invoices.CounterBusyError):
        # Let Razorpay retry: forget the event so the retry is processed.
        logger.exception("billing webhook: storage error on %s", event)
        try:
            billing_events_container.delete_item(item=event_id, partition_key=event_id)
        except exceptions.CosmosHttpResponseError:
            logger.exception("billing webhook: could not release event %s", event_id)
        return jsonify({"error": "Temporary storage error"}), 500

    return jsonify({"status": "ok"}), 200


# ── Tax invoices ──────────────────────────────────────────────────────────────
@billing_blueprint.route("/billing/invoices", methods=["GET"])
@require_role("Admin")
def list_tax_invoices():
    """The signed-in tenant's subscription tax invoices, newest first."""
    return jsonify({"invoices": tax_invoices.list_for_tenant(getattr(request, "tenant_id", None))}), 200


@billing_blueprint.route("/billing/invoices/<invoice_id>/pdf", methods=["GET"])
@require_role("Admin")
def download_tax_invoice(invoice_id):
    """One tax invoice as a PDF; the tenant partition makes another tenant's invoice a 404."""
    tenant_id = getattr(request, "tenant_id", None)
    try:
        invoice = tax_invoices_container.read_item(item=invoice_id, partition_key=tenant_id)
    except exceptions.CosmosResourceNotFoundError:
        invoice = None
    if not invoice or invoice.get("tenant_id") != tenant_id:
        return jsonify({"error": "Invoice not found"}), 404
    pdf = build_tax_invoice_pdf(invoice)
    filename = invoice.get("number", invoice_id).replace("/", "-")
    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response

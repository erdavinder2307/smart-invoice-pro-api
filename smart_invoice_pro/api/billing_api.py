"""
Billing API — Option C: Zoho Payments renewal payment links for SaaS subscriptions.

Reuses the existing Zoho Payments OAuth2 integration to:
  1. Generate a subscription payment link for the chosen plan
  2. Handle Zoho webhook confirming payment → activate subscription
  3. List billing invoices issued to this tenant

Routes:
  POST /api/billing/create-payment-link   — Generate Zoho payment link for subscription
  POST /api/billing/webhook               — Zoho webhook: confirm payment → activate sub
  GET  /api/billing/invoices              — List billing invoices for tenant
  GET  /api/billing/invoices/<id>/download — Download billing invoice (JSON for now)
"""
import os
import uuid
import hmac
import hashlib
import logging
import requests
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request, g

from smart_invoice_pro.services.subscription_service import (
    get_subscription,
    get_plan,
    activate_subscription,
)
from smart_invoice_pro.utils.cosmos_client import billing_invoices_container
from smart_invoice_pro.utils.permission_checker import require_permission

logger = logging.getLogger(__name__)
billing_blueprint = Blueprint("billing", __name__)

ZOHO_ACCOUNTS_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_PAYMENTS_BASE = "https://payments.zoho.com/api/v1"

_COSMOS_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_KEYS}


def _tenant_id() -> str:
    return getattr(g, "tenant_id", None) or getattr(request, "tenant_id", None)


def _get_zoho_access_token() -> str:
    resp = requests.post(ZOHO_ACCOUNTS_URL, data={
        "grant_type": "refresh_token",
        "client_id": os.getenv("ZOHO_PAYMENTS_CLIENT_ID"),
        "client_secret": os.getenv("ZOHO_PAYMENTS_CLIENT_SECRET"),
        "refresh_token": os.getenv("ZOHO_PAYMENTS_REFRESH_TOKEN"),
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Zoho token error: {data}")
    return data["access_token"]


def _zoho_headers() -> dict:
    return {
        "Authorization": f"Zoho-oauthtoken {_get_zoho_access_token()}",
        "Content-Type": "application/json",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_billing_invoice(tenant_id: str, plan: dict, billing_cycle: str,
                             amount_inr: float, payment_link_id: str, payment_link_url: str) -> dict:
    """Record a billing invoice in billing_invoices_container."""
    gst_rate = 0.18
    base = round(amount_inr / (1 + gst_rate), 2)
    gst = round(amount_inr - base, 2)

    invoice = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "plan_id": plan.get("id"),
        "plan_name": plan.get("name", "Starter"),
        "billing_cycle": billing_cycle,
        "amount_inr": base,
        "gst_inr": gst,
        "total_inr": amount_inr,
        "currency": "INR",
        "gst_rate": gst_rate,
        "sac_code": "998314",
        "status": "pending",
        "payment_link_id": payment_link_id,
        "payment_link_url": payment_link_url,
        "issued_at": _now_iso(),
        "due_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "paid_at": None,
        "created_at": _now_iso(),
    }
    billing_invoices_container.create_item(body=invoice)
    return invoice


# ── POST /api/billing/create-payment-link ────────────────────────────────────

@billing_blueprint.route("/billing/create-payment-link", methods=["POST"])
@require_permission("billing", "manage")
def create_subscription_payment_link():
    """
    Generate a Zoho payment link for a subscription plan.
    Body: { plan_id: string, billing_cycle: "monthly" | "annual" }
    Returns: { payment_url, payment_link_id, billing_invoice_id, amount_inr }
    ---
    responses:
      200:
        description: Payment link created
      400:
        description: Missing or invalid parameters
    """
    tenant_id = _tenant_id()
    body = request.get_json(silent=True) or {}
    plan_id = (body.get("plan_id") or "").strip()
    billing_cycle = (body.get("billing_cycle") or "monthly").strip()

    if not plan_id:
        return jsonify({"error": "plan_id is required"}), 400
    if billing_cycle not in ("monthly", "annual"):
        return jsonify({"error": "billing_cycle must be 'monthly' or 'annual'"}), 400

    plan = get_plan(plan_id)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    pricing = plan.get("pricing", {})
    amount_inr = pricing.get("inr_annual" if billing_cycle == "annual" else "inr_monthly")
    if not amount_inr:
        return jsonify({"error": "Pricing not configured for this plan"}), 400

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    reference_id = f"sub_{tenant_id}_{billing_cycle}_{plan_id}"

    payload = {
        "title": f"Solidev Books — {plan.get('name', 'Starter')} ({billing_cycle.capitalize()})",
        "description": (
            f"Subscription to Solidev Books {plan.get('name', 'Starter')} plan "
            f"({'Monthly' if billing_cycle == 'monthly' else 'Annual'} billing)"
        ),
        "amount": amount_inr,
        "currency": "INR",
        "reference_id": reference_id,
        "expiry_time": 72,
        "redirect_url": f"{frontend_url}/settings/billing?payment=success&plan={plan_id}",
        "cancel_url": f"{frontend_url}/settings/billing/upgrade?payment=cancelled",
        "custom_fields": [
            {"label": "Tenant ID", "value": tenant_id},
            {"label": "Plan ID", "value": plan_id},
            {"label": "Billing Cycle", "value": billing_cycle},
        ],
    }

    try:
        zoho_resp = requests.post(
            f"{ZOHO_PAYMENTS_BASE}/paymentlinks",
            json=payload,
            headers=_zoho_headers(),
            timeout=15,
        )
        zoho_resp.raise_for_status()
        zoho_data = zoho_resp.json()
    except requests.HTTPError as e:
        logger.error(f"[billing_api] Zoho payment link error: {e}")
        return jsonify({"error": "Payment gateway error", "details": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    payment_link_id = zoho_data.get("payment_link_id") or zoho_data.get("id")
    payment_url = zoho_data.get("short_url") or zoho_data.get("link_url") or zoho_data.get("url")

    billing_invoice = _create_billing_invoice(
        tenant_id, plan, billing_cycle, amount_inr, payment_link_id, payment_url
    )

    return jsonify({
        "payment_url": payment_url,
        "payment_link_id": payment_link_id,
        "billing_invoice_id": billing_invoice["id"],
        "amount_inr": amount_inr,
        "plan_name": plan.get("name"),
        "billing_cycle": billing_cycle,
    })


# ── POST /api/billing/webhook ─────────────────────────────────────────────────

@billing_blueprint.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    """
    Zoho Payments webhook for subscription payment confirmation.
    Activates the subscription on payment.success.
    This endpoint is exempt from JWT auth (webhook receiver).
    ---
    responses:
      200:
        description: Webhook processed
    """
    webhook_secret = os.getenv("ZOHO_PAYMENTS_WEBHOOK_SECRET")
    if webhook_secret:
        sig_header = request.headers.get("X-Zoho-Payments-Signature", "")
        body_bytes = request.get_data()
        expected = hmac.new(
            webhook_secret.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            return jsonify({"error": "Invalid signature"}), 401

    event = request.json or {}
    event_type = event.get("event_type") or event.get("type")
    data = event.get("data") or event.get("payload") or {}

    if event_type not in ("payment.success", "payment_link.paid", "payment.completed"):
        return jsonify({"status": "ignored"})

    reference_id = (
        data.get("reference_id")
        or data.get("payment_link", {}).get("reference_id")
        or ""
    )
    payment_link_id = data.get("payment_link_id") or data.get("id")
    amount_paid = float(data.get("amount") or data.get("amount_paid") or 0)

    # Parse tenant_id and plan from custom_fields or reference_id
    # reference_id format: sub_{tenant_id}_{billing_cycle}_{plan_id}
    tenant_id = None
    plan_id = None
    billing_cycle = "monthly"

    custom_fields = data.get("custom_fields") or []
    for field in custom_fields:
        label = (field.get("label") or "").lower()
        value = field.get("value") or ""
        if label == "tenant id":
            tenant_id = value
        elif label == "plan id":
            plan_id = value
        elif label == "billing cycle":
            billing_cycle = value

    if not tenant_id and reference_id.startswith("sub_"):
        parts = reference_id.split("_", 3)
        if len(parts) >= 4:
            tenant_id = parts[1]
            billing_cycle = parts[2]
            plan_id = parts[3]

    if not tenant_id or not plan_id:
        logger.error(f"[billing_api] webhook: could not extract tenant_id/plan_id from {reference_id}")
        return jsonify({"status": "ok", "warning": "could not process subscription activation"})

    # Update billing invoice status
    try:
        items = list(billing_invoices_container.query_items(
            query="SELECT * FROM c WHERE c.payment_link_id = @plid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@plid", "value": payment_link_id}],
            partition_key=tenant_id,
        ))
        if items:
            inv = items[0]
            inv["status"] = "paid"
            inv["paid_at"] = _now_iso()
            billing_invoices_container.replace_item(item=inv["id"], body=inv)
    except Exception as e:
        logger.error(f"[billing_api] billing invoice update failed: {e}")

    # Activate the subscription
    try:
        activate_subscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            billing_cycle=billing_cycle,
            amount_inr=amount_paid,
            gateway="zoho_payments",
            gateway_sub_id=payment_link_id,
        )
        logger.info(f"[billing_api] Subscription activated for tenant {tenant_id}")
    except Exception as e:
        logger.error(f"[billing_api] activate_subscription failed: {e}")

    return jsonify({"status": "ok"})


# ── GET /api/billing/invoices ─────────────────────────────────────────────────

@billing_blueprint.route("/billing/invoices", methods=["GET"])
@require_permission("billing", "view")
def list_billing_invoices():
    """
    List all platform billing invoices for the current tenant.
    ---
    responses:
      200:
        description: List of billing invoices
    """
    tenant_id = _tenant_id()
    try:
        items = list(billing_invoices_container.query_items(
            query="SELECT * FROM c WHERE c.tenant_id = @tid ORDER BY c.issued_at DESC",
            parameters=[{"name": "@tid", "value": tenant_id}],
            partition_key=tenant_id,
        ))
        return jsonify({"invoices": [_clean(i) for i in items], "count": len(items)})
    except Exception as e:
        logger.error(f"[billing_api] list_billing_invoices failed: {e}")
        return jsonify({"invoices": [], "count": 0})


# ── GET /api/billing/invoices/<invoice_id> ────────────────────────────────────

@billing_blueprint.route("/billing/invoices/<invoice_id>", methods=["GET"])
@require_permission("billing", "view")
def get_billing_invoice(invoice_id: str):
    """Return a single billing invoice by id."""
    tenant_id = _tenant_id()
    try:
        items = list(billing_invoices_container.query_items(
            query="SELECT * FROM c WHERE c.id = @iid AND c.tenant_id = @tid OFFSET 0 LIMIT 1",
            parameters=[
                {"name": "@iid", "value": invoice_id},
                {"name": "@tid", "value": tenant_id},
            ],
            partition_key=tenant_id,
        ))
        if not items:
            return jsonify({"error": "Invoice not found"}), 404
        return jsonify(_clean(items[0]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

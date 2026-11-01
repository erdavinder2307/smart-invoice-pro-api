"""
Subscription API — tenant-facing subscription management.
All routes require a valid JWT (enforced globally by auth_middleware).
"""
import uuid
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g

from smart_invoice_pro.services.subscription_service import (
    get_subscription,
    get_current_usage,
    get_entitlements,
    request_extension,
    cancel_subscription,
    get_plan,
)
from smart_invoice_pro.utils.cosmos_client import (
    subscriptions_container,
    trial_extension_requests_container,
)
from smart_invoice_pro.utils.permission_checker import require_permission

logger = logging.getLogger(__name__)
subscription_blueprint = Blueprint("subscription", __name__)

_COSMOS_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_KEYS}


def _tenant_id() -> str:
    return getattr(g, "tenant_id", None) or getattr(request, "tenant_id", None)


# ── GET /api/subscription/current ────────────────────────────────────────────

@subscription_blueprint.route("/subscription/current", methods=["GET"])
def get_current_subscription():
    """
    Return the active subscription, plan details, trial status, and usage.
    ---
    responses:
      200:
        description: Subscription details
      404:
        description: No subscription found
    """
    tenant_id = _tenant_id()
    sub = get_subscription(tenant_id)
    if not sub:
        return jsonify({"error": "No subscription found"}), 404

    plan = get_plan(sub.get("plan_id")) or {}
    usage = get_current_usage(tenant_id)
    entitlements = get_entitlements(tenant_id)

    trial_ends_at = sub.get("trial_ends_at")
    days_remaining = None
    if trial_ends_at and sub.get("status", "") in {"trial", "trial_extension_1", "trial_extension_2", "founding_customer"}:
        try:
            end = datetime.fromisoformat(trial_ends_at.replace("Z", "+00:00"))
            days_remaining = max(0, (end - datetime.now(timezone.utc)).days)
        except Exception:
            pass

    return jsonify({
        "subscription": _clean(sub),
        "plan": _clean(plan),
        "trial_days_remaining": days_remaining,
        "usage": usage.get("metrics", {}),
        "entitlements": entitlements,
    })


# ── GET /api/subscription/usage ──────────────────────────────────────────────

@subscription_blueprint.route("/subscription/usage", methods=["GET"])
def get_usage():
    """
    Return current period usage vs plan limits.
    ---
    responses:
      200:
        description: Usage metrics and limits
    """
    tenant_id = _tenant_id()
    entitlements = get_entitlements(tenant_id)
    usage = get_current_usage(tenant_id)
    return jsonify({
        "usage": usage.get("metrics", {}),
        "limits": entitlements.get("limits", {}),
        "entitlements": entitlements,
        "period_start": usage.get("billing_period_start"),
        "period_end": usage.get("billing_period_end"),
    })


# ── POST /api/subscription/request-extension ─────────────────────────────────

@subscription_blueprint.route("/subscription/request-extension", methods=["POST"])
@require_permission("billing", "view")
def request_trial_extension():
    """
    Request a trial extension.
    Body: { extension_type: "1" | "2", reason: string }
    Extension 1 auto-approves. Extension 2 queues for admin.
    ---
    responses:
      200:
        description: Extension result
      400:
        description: Validation error
    """
    tenant_id = _tenant_id()
    body = request.get_json(silent=True) or {}
    ext_type = str(body.get("extension_type", "")).strip()
    reason = (body.get("reason") or "").strip()

    if ext_type not in ("1", "2"):
        return jsonify({"error": "extension_type must be '1' or '2'"}), 400

    try:
        result = request_extension(tenant_id, ext_type, reason)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[subscription_api] request_extension failed: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── POST /api/subscription/cancel ────────────────────────────────────────────

@subscription_blueprint.route("/subscription/cancel", methods=["POST"])
@require_permission("billing", "manage")
def cancel():
    """
    Cancel the subscription. Begins 7-day grace period; data retained 90 days.
    Body: { reason: string }
    ---
    responses:
      200:
        description: Cancellation recorded
    """
    tenant_id = _tenant_id()
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()

    try:
        sub = cancel_subscription(tenant_id, reason)
        return jsonify({"status": "cancelled", "cancelled_at": sub.get("cancelled_at")})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[subscription_api] cancel failed: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── GET /api/subscription/extension-requests ─────────────────────────────────

@subscription_blueprint.route("/subscription/extension-requests", methods=["GET"])
@require_permission("billing", "view")
def list_my_extension_requests():
    """Return all extension requests for the current tenant."""
    tenant_id = _tenant_id()
    try:
        items = list(trial_extension_requests_container.query_items(
            query="SELECT * FROM c WHERE c.tenant_id = @tid ORDER BY c.requested_at DESC",
            parameters=[{"name": "@tid", "value": tenant_id}],
            partition_key=tenant_id,
        ))
        return jsonify({"requests": [_clean(r) for r in items]})
    except Exception as e:
        logger.error(f"[subscription_api] list_extension_requests failed: {e}")
        return jsonify({"requests": []})

"""
Plans API — public endpoint, no auth required.
GET /api/plans
"""
from flask import Blueprint, jsonify
from smart_invoice_pro.services.subscription_service import get_public_plans

plans_blueprint = Blueprint("plans", __name__)

_COSMOS_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_KEYS}


@plans_blueprint.route("/plans", methods=["GET"])
def list_plans():
    """
    List all active public plans.
    ---
    responses:
      200:
        description: Array of plan objects with features and pricing
    """
    plans = [_clean(p) for p in get_public_plans()]
    return jsonify({"plans": plans, "count": len(plans)})

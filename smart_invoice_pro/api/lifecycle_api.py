from flask import Blueprint, jsonify, request

from smart_invoice_pro.utils.cosmos_client import (
    audit_logs_container,
    bank_accounts_container,
    bills_container,
    customers_container,
    expenses_container,
    invoices_container,
    notifications_container,
    products_container,
    purchase_orders_container,
    quotes_container,
    recurring_profiles_container,
    sales_orders_container,
    settings_container,
    users_container,
    vendors_container,
)
from smart_invoice_pro.utils.lifecycle_service import (
    apply_lifecycle_action,
    compute_lifecycle_analysis,
    is_archived,
    normalize_entity_type,
)


lifecycle_blueprint = Blueprint("lifecycle", __name__)


ENTITY_CONTAINER_MAP = {
    "product": products_container,
    "customer": customers_container,
    "vendor": vendors_container,
    "quote": quotes_container,
    "invoice": invoices_container,
    "sales_order": sales_orders_container,
    "purchase_order": purchase_orders_container,
    "bill": bills_container,
    "expense": expenses_container,
    "recurring_profile": recurring_profiles_container,
    "bank_account": bank_accounts_container,
    "tax_rate": settings_container,
    "role": settings_container,
    "user": users_container,
    "notification": notifications_container,
    "audit_log": audit_logs_container,
}


def _resolve_container(entity_type):
    normalized = normalize_entity_type(entity_type)
    container = ENTITY_CONTAINER_MAP.get(normalized)
    return normalized, container


def _load_entity(container, entity_id, tenant_id):
    rows = list(container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
        parameters=[
            {"name": "@id", "value": entity_id},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return rows[0] if rows else None


@lifecycle_blueprint.route("/lifecycle/<entity_type>/<entity_id>/analysis", methods=["GET"])
def lifecycle_analysis(entity_type, entity_id):
    normalized, container = _resolve_container(entity_type)
    if not container:
        return jsonify({"error": f"Unsupported entity type: {entity_type}"}), 400

    item = _load_entity(container, entity_id, request.tenant_id)
    if not item:
        return jsonify({"error": f"{normalized} not found"}), 404

    analysis = compute_lifecycle_analysis(normalized, entity_id, request.tenant_id)
    analysis.update({
        "entityLabel": normalized.replace("_", " ").title(),
        "isArchived": is_archived(item),
    })
    return jsonify(analysis), 200


@lifecycle_blueprint.route("/lifecycle/<entity_type>/<entity_id>/execute", methods=["POST"])
def lifecycle_execute(entity_type, entity_id):
    payload = request.get_json() or {}
    requested_action = str(payload.get("action") or "delete").strip().lower()

    if requested_action not in {"delete", "archive", "restore"}:
        return jsonify({"error": "Invalid action. Allowed: delete, archive, restore"}), 400

    normalized, container = _resolve_container(entity_type)
    if not container:
        return jsonify({"error": f"Unsupported entity type: {entity_type}"}), 400

    item = _load_entity(container, entity_id, request.tenant_id)
    if not item:
        return jsonify({"error": f"{normalized} not found"}), 404

    if requested_action == "restore" and not is_archived(item):
        return jsonify({"error": f"{normalized} is not archived"}), 422

    result = apply_lifecycle_action(
        container=container,
        item=item,
        entity_type=normalized,
        tenant_id=request.tenant_id,
        user_id=getattr(request, "user_id", None),
        requested_action=requested_action,
        reason=payload.get("reason") or "lifecycle_execute",
    )

    return jsonify({
        "entityType": normalized,
        "entityId": entity_id,
        "requestedAction": result.get("requestedAction"),
        "performedAction": result.get("performedAction"),
        "status": result.get("status"),
        "dependencySummary": result.get("dependencySummary", {}),
        "hardDeleteAllowed": bool(result.get("hardDeleteAllowed")),
        "message": "Record permanently deleted" if result.get("performedAction") == "delete" else "Record archived",
    }), 200


@lifecycle_blueprint.route("/lifecycle/<entity_type>/bulk-execute", methods=["POST"])
def lifecycle_bulk_execute(entity_type):
    payload = request.get_json() or {}
    ids = payload.get("ids") or []
    requested_action = str(payload.get("action") or "delete").strip().lower()

    if requested_action not in {"delete", "archive", "restore"}:
        return jsonify({"error": "Invalid action. Allowed: delete, archive, restore"}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids must be a non-empty array"}), 400

    normalized, container = _resolve_container(entity_type)
    if not container:
        return jsonify({"error": f"Unsupported entity type: {entity_type}"}), 400

    summary = {
        "entityType": normalized,
        "requestedAction": requested_action,
        "requestedCount": len(ids),
        "processedCount": 0,
        "deletedCount": 0,
        "archivedCount": 0,
        "restoredCount": 0,
        "failedCount": 0,
        "dependencySummary": {},
        "results": [],
    }

    for entity_id in ids:
        item = _load_entity(container, entity_id, request.tenant_id)
        if not item:
            summary["failedCount"] += 1
            summary["results"].append({
                "id": entity_id,
                "success": False,
                "error": "NOT_FOUND",
            })
            continue

        if requested_action == "restore" and not is_archived(item):
            summary["failedCount"] += 1
            summary["results"].append({
                "id": entity_id,
                "success": False,
                "error": "NOT_ARCHIVED",
            })
            continue

        try:
            result = apply_lifecycle_action(
                container=container,
                item=item,
                entity_type=normalized,
                tenant_id=request.tenant_id,
                user_id=getattr(request, "user_id", None),
                requested_action=requested_action,
                reason="lifecycle_bulk_execute",
            )

            summary["processedCount"] += 1
            performed = result.get("performedAction")
            if performed == "delete":
                summary["deletedCount"] += 1
            elif performed == "archive":
                summary["archivedCount"] += 1
            elif performed == "restore":
                summary["restoredCount"] += 1

            for key, value in (result.get("dependencySummary") or {}).items():
                summary["dependencySummary"][key] = int(summary["dependencySummary"].get(key, 0)) + int(value or 0)

            summary["results"].append({
                "id": entity_id,
                "success": True,
                "performedAction": performed,
                "dependencySummary": result.get("dependencySummary", {}),
            })
        except Exception as exc:
            summary["failedCount"] += 1
            summary["results"].append({
                "id": entity_id,
                "success": False,
                "error": str(exc),
            })

    return jsonify(summary), 200

from copy import deepcopy
from datetime import datetime

from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity
from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
from smart_invoice_pro.utils.domain_events import record_domain_event


ENTITY_DELETED = "ENTITY_DELETED"

# Entities that must never be physically deleted to preserve accounting and audit traceability.
ACCOUNTING_PROTECTED_ENTITIES = {
    "invoice",
    "payment",
    "reconciliation",
    "bill",
    "purchase_order",
    "expense",
    "tax_rate",
    "tax",
    "audit_log",
    "workflow_history",
    "event_log",
    "domain_event",
}

ENTITY_PARTITION_KEY_FIELD = {
    "product": "product_id",
    "customer": "customer_id",
    "vendor": "vendor_id",
    "quote": "customer_id",
    "invoice": "customer_id",
    "sales_order": "customer_id",
    "purchase_order": "vendor_id",
    "bill": "vendor_id",
    "expense": "id",
    "recurring_profile": "customer_id",
    "bank_account": "user_id",
    "tax_rate": "tenant_id",
    "role": "tenant_id",
    "user": "userid",
    "notification": "tenant_id",
    "audit_log": "tenant_id",
}


def normalize_entity_type(entity_type):
    value = str(entity_type or "").strip().lower().replace("-", "_")

    aliases = {
        "products": "product",
        "items": "product",
        "item": "product",
        "customers": "customer",
        "vendors": "vendor",
        "quotes": "quote",
        "invoices": "invoice",
        "sales_orders": "sales_order",
        "salesorders": "sales_order",
        "salesorder": "sales_order",
        "purchase_orders": "purchase_order",
        "purchaseorders": "purchase_order",
        "purchaseorder": "purchase_order",
        "bills": "bill",
        "expenses": "expense",
        "recurring_profiles": "recurring_profile",
        "recurringprofile": "recurring_profile",
        "recurring_invoices": "recurring_profile",
        "bank_accounts": "bank_account",
        "bankaccounts": "bank_account",
        "tax_rates": "tax_rate",
        "taxrates": "tax_rate",
        "roles": "role",
        "users": "user",
        "notifications": "notification",
        "audit_logs": "audit_log",
    }

    return aliases.get(value, value)


def is_archived(item):
    if not isinstance(item, dict):
        return False
    status = str(item.get("status") or item.get("lifecycle_status") or "").upper()
    return status == "ARCHIVED" or bool(item.get("is_deleted", False))


def compute_lifecycle_analysis(entity_type, entity_id, tenant_id):
    normalized_type = normalize_entity_type(entity_type)
    dependency = check_entity_dependencies(normalized_type, entity_id, tenant_id)
    has_dependencies = bool(dependency.get("hasDependencies"))

    hard_delete_allowed = not has_dependencies and normalized_type not in ACCOUNTING_PROTECTED_ENTITIES
    recommended_action = "delete" if hard_delete_allowed else "archive"

    return {
        "entityType": normalized_type,
        "entityId": entity_id,
        "hasDependencies": has_dependencies,
        "dependencySummary": dependency.get("dependencySummary", {}),
        "hardDeleteAllowed": hard_delete_allowed,
        "recommendedAction": recommended_action,
        "isAccountingProtected": normalized_type in ACCOUNTING_PROTECTED_ENTITIES,
    }


def _resolve_partition_key(item, entity_type):
    partition_key_field = ENTITY_PARTITION_KEY_FIELD.get(normalize_entity_type(entity_type))
    if partition_key_field:
        if partition_key_field in item and item.get(partition_key_field) is not None:
            return item.get(partition_key_field)

    for fallback_field in ("tenant_id", "user_id", "customer_id", "vendor_id", "product_id", "id"):
        if fallback_field in item and item.get(fallback_field) is not None:
            return item.get(fallback_field)

    return item.get("id")


def hard_delete_entity(container, item, entity_type, tenant_id, user_id=None, reason=None):
    before_snapshot = deepcopy(item)
    partition_key_value = _resolve_partition_key(item, entity_type)

    container.delete_item(item=item["id"], partition_key=partition_key_value)

    log_audit_event({
        "action": "ENTITY_DELETED",
        "entity": entity_type,
        "entity_id": item.get("id"),
        "before": before_snapshot,
        "after": None,
        "metadata": {
            "event": "entity_deleted",
            "reason": reason,
            "partition_key": partition_key_value,
        },
        "tenant_id": tenant_id,
        "user_id": user_id,
    })

    record_domain_event(
        ENTITY_DELETED,
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=item.get("id"),
        payload={
            "reason": reason,
            "partition_key": partition_key_value,
        },
    )

    return {
        "id": item.get("id"),
        "action": "delete",
        "status": "DELETED",
    }


def apply_lifecycle_action(container, item, entity_type, tenant_id, user_id=None, requested_action="delete", reason=None):
    normalized_type = normalize_entity_type(entity_type)
    requested = str(requested_action or "delete").strip().lower()

    if requested == "restore":
        restored = restore_entity(
            container,
            item,
            normalized_type,
            tenant_id,
            user_id=user_id,
            reason=reason,
        )
        return {
            "requestedAction": "restore",
            "performedAction": "restore",
            "status": restored.get("status"),
            "dependencySummary": {},
            "hardDeleteAllowed": False,
        }

    if requested == "archive":
        archived = archive_entity(
            container,
            item,
            normalized_type,
            tenant_id,
            user_id=user_id,
            reason=reason,
        )
        return {
            "requestedAction": "archive",
            "performedAction": "archive",
            "status": archived.get("status"),
            "dependencySummary": {},
            "hardDeleteAllowed": False,
        }

    # requested == delete -> apply smart decision
    analysis = compute_lifecycle_analysis(normalized_type, item.get("id"), tenant_id)

    if analysis["hardDeleteAllowed"]:
        deleted = hard_delete_entity(
            container,
            item,
            normalized_type,
            tenant_id,
            user_id=user_id,
            reason=reason or "smart_delete_no_dependencies",
        )
        return {
            "requestedAction": "delete",
            "performedAction": "delete",
            "status": deleted.get("status"),
            "dependencySummary": analysis.get("dependencySummary", {}),
            "hardDeleteAllowed": True,
        }

    archived = archive_entity(
        container,
        item,
        normalized_type,
        tenant_id,
        user_id=user_id,
        reason=reason or "smart_archive_due_to_dependencies_or_policy",
    )
    return {
        "requestedAction": "delete",
        "performedAction": "archive",
        "status": archived.get("status"),
        "dependencySummary": analysis.get("dependencySummary", {}),
        "hardDeleteAllowed": False,
    }

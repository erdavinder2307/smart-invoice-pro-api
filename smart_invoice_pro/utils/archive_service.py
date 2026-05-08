from datetime import datetime
from copy import deepcopy

from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.domain_events import ENTITY_ARCHIVED, ENTITY_RESTORED, record_domain_event


LIFECYCLE_ARCHIVED = "ARCHIVED"
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_RESTORED = "RESTORED"


def archive_entity(container, item, entity_type, tenant_id, user_id=None, reason=None):
    before_snapshot = deepcopy(item)
    now = datetime.utcnow().isoformat()

    item["status"] = LIFECYCLE_ARCHIVED
    item["archived_at"] = now
    item["archived_by"] = user_id
    item["updated_at"] = now

    # Backward compatibility for existing product soft-delete behavior.
    if str(entity_type).strip().lower() in {"product", "item"}:
        item["is_deleted"] = True
        item["deleted_at"] = now

    container.replace_item(item=item["id"], body=item)

    log_audit_event({
        "action": "ENTITY_ARCHIVED",
        "entity": entity_type,
        "entity_id": item.get("id"),
        "before": before_snapshot,
        "after": item,
        "metadata": {
            "event": "entity_archived",
            "reason": reason,
            "lifecycle_status": LIFECYCLE_ARCHIVED,
        },
        "tenant_id": tenant_id,
        "user_id": user_id,
    })

    record_domain_event(
        ENTITY_ARCHIVED,
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=item.get("id"),
        payload={
            "reason": reason,
            "lifecycle_status": LIFECYCLE_ARCHIVED,
        },
    )

    return item
    return item


def restore_entity(container, item, entity_type, tenant_id, user_id=None, reason=None):
    """Restore an archived entity back to ACTIVE lifecycle state."""
    before_snapshot = deepcopy(item)
    now = datetime.utcnow().isoformat()

    item["status"] = LIFECYCLE_ACTIVE
    item["archived_at"] = None
    item["archived_by"] = None
    item["restored_at"] = now
    item["restored_by"] = user_id
    item["updated_at"] = now

    # Reverse backward-compat soft-delete flag for products.
    if str(entity_type).strip().lower() in {"product", "item"}:
        item["is_deleted"] = False
        item["deleted_at"] = None

    container.replace_item(item=item["id"], body=item)

    log_audit_event({
        "action": "ENTITY_RESTORED",
        "entity": entity_type,
        "entity_id": item.get("id"),
        "before": before_snapshot,
        "after": item,
        "metadata": {
            "event": "entity_restored",
            "reason": reason,
            "lifecycle_status": LIFECYCLE_ACTIVE,
        },
        "tenant_id": tenant_id,
        "user_id": user_id,
    })

    record_domain_event(
        ENTITY_RESTORED,
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=item.get("id"),
        payload={
            "reason": reason,
            "lifecycle_status": LIFECYCLE_ACTIVE,
        },
    )

    return item

import uuid
from datetime import datetime

from smart_invoice_pro.utils.cosmos_client import domain_events_container


ENTITY_ARCHIVED = "ENTITY_ARCHIVED"
ENTITY_RESTORED = "ENTITY_RESTORED"
BULK_ARCHIVE_COMPLETED = "BULK_ARCHIVE_COMPLETED"
BULK_RESTORE_COMPLETED = "BULK_RESTORE_COMPLETED"


def record_domain_event(event_type, tenant_id, user_id=None, entity_type=None, entity_id=None, payload=None):
    if not tenant_id:
        return

    doc = {
        "id": str(uuid.uuid4()),
        "event_type": str(event_type or "").strip().upper(),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "entity_type": str(entity_type or "").strip().lower() or None,
        "entity_id": entity_id,
        "payload": payload or {},
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        domain_events_container.create_item(body=doc)
    except Exception:
        # Domain events are best-effort in this phase; archive operations should not fail on event write errors.
        return


def record_bulk_archive_completed(tenant_id, user_id, entity_type, result):
    payload = {
        "successCount": int(result.get("successCount", 0)),
        "failedCount": int(result.get("failedCount", 0)),
        "requestedCount": int(result.get("requestedCount", 0)),
        "dependencySummary": result.get("dependencySummary", {}),
        "classification": result.get("classification", {}),
    }
    record_domain_event(
        BULK_ARCHIVE_COMPLETED,
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=entity_type,
        payload=payload,
    )

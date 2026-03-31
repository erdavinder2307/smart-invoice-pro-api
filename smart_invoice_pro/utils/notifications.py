import uuid
import logging
from datetime import datetime
from smart_invoice_pro.utils.cosmos_client import notifications_container

logger = logging.getLogger(__name__)


def create_notification(
    tenant_id,
    notification_type,
    title,
    message,
    entity_id=None,
    entity_type=None,
    user_id=None,
):
    """
    Fire-and-forget helper. Inserts a notification document into Cosmos DB.
    Failures are logged and swallowed so they never break the calling operation.
    """
    if not tenant_id:
        return

    try:
        doc = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "type": notification_type,
            "title": title,
            "message": message,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "is_read": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        notifications_container.create_item(body=doc)
    except Exception as exc:
        logger.warning(f"[notifications] Failed to create notification: {exc}")

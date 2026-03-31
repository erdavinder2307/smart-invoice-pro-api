"""
Audit Logger — fire-and-forget helper.

Usage:
    log_audit(
        entity_type="invoice",
        action="update",
        entity_id=invoice_id,
        before=snapshot_before,
        after=snapshot_after,
        user_id=request.user_id,
        tenant_id=request.tenant_id,
    )

Sensitive fields (passwords, tokens) are stripped before storage.
Records are immutable — the API only exposes GET.
"""
import copy
import uuid
import logging
from datetime import datetime

from smart_invoice_pro.utils.cosmos_client import audit_logs_container
from smart_invoice_pro.utils.response_sanitizer import sanitize_item

logger = logging.getLogger(__name__)

# Fields never stored in audit logs
_AUDIT_SENSITIVE = {
    "password",
    "portal_password",
    "portal_token",
    "token",
    "refresh_token",
    "hashed_pw",
}


def _clean(doc):
    """Strip sensitive + Cosmos internal fields from a document snapshot."""
    if doc is None:
        return None
    if not isinstance(doc, dict):
        return doc
    return sanitize_item(doc, additional_sensitive_fields=_AUDIT_SENSITIVE)


def log_audit(
    entity_type: str,
    action: str,
    entity_id: str,
    before,
    after,
    *,
    user_id: str = None,
    tenant_id: str = None,
):
    """
    Persist an audit log entry.

    Parameters
    ----------
    entity_type : str   "invoice" | "customer" | "payment" | "user"
    action      : str   "create" | "update" | "delete"
    entity_id   : str   The ID of the affected record
    before      : dict  State before the change (None for creates)
    after       : dict  State after the change  (None for deletes)
    user_id     : str   Actor (from JWT)
    tenant_id   : str   Tenant scope (mandatory — logs not written without it)
    """
    if not tenant_id:
        return

    try:
        doc = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id else None,
            "changes": {
                "before": _clean(copy.deepcopy(before)) if before is not None else None,
                "after": _clean(copy.deepcopy(after)) if after is not None else None,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
        audit_logs_container.create_item(body=doc)
    except Exception as exc:
        logger.warning(f"[audit] Failed to write audit log: {exc}")

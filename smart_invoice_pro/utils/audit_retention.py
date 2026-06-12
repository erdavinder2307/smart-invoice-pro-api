"""Audit log retention — archive expired records to cold storage."""

import logging
import os
from datetime import datetime, timedelta

from smart_invoice_pro.utils.cosmos_client import audit_logs_archive_container, audit_logs_container

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "0") or "0")
ARCHIVE_BATCH_SIZE = int(os.getenv("AUDIT_LOG_ARCHIVE_BATCH", "200") or "200")


def retention_days():
    """Return configured retention days; 0 disables archival."""
    return max(0, DEFAULT_RETENTION_DAYS)


def _cutoff_iso(days):
    cutoff = datetime.utcnow() - timedelta(days=days)
    return cutoff.isoformat()


def archive_expired_audit_logs(*, tenant_id=None, retention_days_override=None):
    """Move audit logs older than retention threshold into audit_logs_archive.

    Returns a summary dict. No-op when retention is disabled (0 days).
    """
    days = retention_days_override if retention_days_override is not None else retention_days()
    if days <= 0:
        return {"enabled": False, "archived": 0, "retention_days": 0}

    cutoff = _cutoff_iso(days)
    conditions = ["(c.created_at < @cutoff OR c.timestamp < @cutoff)"]
    params = [{"name": "@cutoff", "value": cutoff}]
    if tenant_id:
        conditions.append("c.tenant_id = @tenant_id")
        params.append({"name": "@tenant_id", "value": tenant_id})

    where_sql = " AND ".join(conditions)
    query = (
        f"SELECT * FROM c WHERE {where_sql} "
        f"ORDER BY c.created_at ASC OFFSET 0 LIMIT {ARCHIVE_BATCH_SIZE}"
    )
    rows = list(
        audit_logs_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    archived = 0
    for row in rows:
        archive_doc = dict(row)
        archive_doc["archived_at"] = datetime.utcnow().isoformat()
        archive_doc["source_container"] = "audit_logs"
        try:
            audit_logs_archive_container.create_item(body=archive_doc)
            audit_logs_container.delete_item(item=row["id"], partition_key=row["tenant_id"])
            archived += 1
        except Exception as exc:
            logger.warning("[audit-retention] failed to archive %s: %s", row.get("id"), exc)

    return {
        "enabled": True,
        "archived": archived,
        "retention_days": days,
        "cutoff": cutoff,
        "batch_size": ARCHIVE_BATCH_SIZE,
    }

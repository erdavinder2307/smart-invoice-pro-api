"""Map domain_events documents into Activity Center feed items."""

_DOMAIN_SUMMARIES = {
    "ENTITY_ARCHIVED": "Archived",
    "ENTITY_RESTORED": "Restored",
    "BULK_ARCHIVE_COMPLETED": "Bulk archive completed",
    "BULK_RESTORE_COMPLETED": "Bulk restore completed",
    "BANK_IMPORT_BATCH_CREATED": "Bank import started",
    "BANK_IMPORT_BATCH_APPROVED": "Bank import approved",
    "BANK_IMPORT_COMPLETED": "Bank import completed",
    "BANK_IMPORT_FAILED": "Bank import failed",
}


def domain_event_to_activity(entry: dict) -> dict:
    """Normalize a domain_events row for Activity Center consumers."""
    if not isinstance(entry, dict):
        return entry

    event_type = str(entry.get("event_type") or "").strip().upper()
    entity_type = str(entry.get("entity_type") or "record").replace("_", " ")
    payload = entry.get("payload") or {}

    base_summary = _DOMAIN_SUMMARIES.get(event_type, event_type.replace("_", " ").title())
    if event_type == "BULK_ARCHIVE_COMPLETED":
        count = payload.get("successCount", payload.get("requestedCount", 0))
        summary = f"{count} {entity_type}(s) archived"
    elif event_type == "BANK_IMPORT_COMPLETED":
        count = payload.get("row_count", 0)
        summary = f"{count} transactions imported" if count else "Bank import completed"
    elif event_type in ("BANK_IMPORT_BATCH_CREATED", "BANK_IMPORT_BATCH_APPROVED"):
        summary = f"{entity_type} — {_DOMAIN_SUMMARIES.get(event_type, base_summary)}"
    elif event_type in ("ENTITY_ARCHIVED", "ENTITY_RESTORED"):
        summary = f"{entity_type} {base_summary.lower()}"
    else:
        summary = f"{entity_type} — {base_summary}"

    return {
        "id": entry.get("id"),
        "tenant_id": entry.get("tenant_id"),
        "user_id": entry.get("user_id"),
        "action": event_type,
        "entity": entry.get("entity_type"),
        "entity_id": entry.get("entity_id"),
        "summary": summary,
        "category": "banking" if str(event_type).startswith("BANK_") else "workflow",
        "risk_level": "medium",
        "metadata": payload,
        "created_at": entry.get("created_at"),
        "timestamp": entry.get("created_at"),
        "source": "domain_event",
        "before": None,
        "after": None,
    }

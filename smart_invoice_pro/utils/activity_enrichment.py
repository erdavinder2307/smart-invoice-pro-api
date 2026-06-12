"""Read-time enrichment for audit / activity log entries.

Backfills display fields on legacy records that were written before Phase 1
enrichment (summary, entity_label, category, risk_level, user_name).
"""
from smart_invoice_pro.utils.audit_logger import (
    _build_summary,
    _infer_entity_label,
    _lookup_user_actor,
    _normalize_action,
    _resolve_category,
    _resolve_risk_level,
)

_USER_CACHE: dict[str, dict] = {}
_TENANT_CACHE: dict[str, str] = {}


def _cached_user_profile(user_id: str) -> dict:
    if not user_id:
        return {}
    if user_id not in _USER_CACHE:
        _USER_CACHE[user_id] = _lookup_user_actor(user_id)
    return _USER_CACHE[user_id]


def clear_user_cache():
    """Testing helper — reset in-process user lookup cache."""
    _USER_CACHE.clear()
    _TENANT_CACHE.clear()


def _lookup_tenant_name(tenant_id: str) -> str | None:
    if not tenant_id:
        return None
    if tenant_id in _TENANT_CACHE:
        return _TENANT_CACHE[tenant_id]
    try:
        from smart_invoice_pro.utils.cosmos_client import tenants_container
        items = list(tenants_container.query_items(
            query="SELECT c.name, c.organization_name FROM c WHERE c.id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        if items:
            row = items[0]
            name = row.get("organization_name") or row.get("name") or tenant_id
            _TENANT_CACHE[tenant_id] = name
            return name
    except Exception:
        return None
    return None


def enrich_audit_entry(entry: dict) -> dict:
    """Return a copy of *entry* with display fields populated when missing."""
    if not isinstance(entry, dict):
        return entry

    enriched = dict(entry)
    entity = str(enriched.get("entity") or enriched.get("entity_type") or "").lower()
    action = _normalize_action(enriched.get("action"))

    before = enriched.get("before")
    if before is None and isinstance(enriched.get("changes"), dict):
        before = enriched["changes"].get("before")
    after = enriched.get("after")
    if after is None and isinstance(enriched.get("changes"), dict):
        after = enriched["changes"].get("after")

    if not enriched.get("entity_label"):
        enriched["entity_label"] = _infer_entity_label(entity, before, after)

    if not enriched.get("summary"):
        enriched["summary"] = _build_summary(entity, action, enriched.get("entity_label"))

    if not enriched.get("category"):
        enriched["category"] = _resolve_category(entity)

    if not enriched.get("risk_level"):
        enriched["risk_level"] = _resolve_risk_level(entity, action)

    if not enriched.get("module"):
        enriched["module"] = entity or "system"

    user_id = enriched.get("user_id")
    if user_id and not enriched.get("user_name") and not enriched.get("user_email"):
        profile = _cached_user_profile(user_id)
        if profile.get("user_name"):
            enriched["user_name"] = profile["user_name"]
        if profile.get("user_email"):
            enriched["user_email"] = profile["user_email"]

    enriched["entity"] = entity or enriched.get("entity")
    enriched["created_at"] = enriched.get("created_at") or enriched.get("timestamp")
    return enriched


def enrich_audit_entries(entries: list) -> list:
    """Batch-enrich audit log entries with a shared user lookup cache."""
    if not entries:
        return []
    return [enrich_audit_entry(entry) for entry in entries]


def enrich_admin_audit_entry(entry: dict) -> dict:
    """Admin feed enrichment — includes tenant display name."""
    enriched = enrich_audit_entry(entry)
    tenant_id = enriched.get("tenant_id")
    if tenant_id and not enriched.get("tenant_name"):
        enriched["tenant_name"] = _lookup_tenant_name(tenant_id) or tenant_id
    return enriched


def enrich_admin_audit_entries(entries: list) -> list:
    if not entries:
        return []
    return [enrich_admin_audit_entry(entry) for entry in entries]

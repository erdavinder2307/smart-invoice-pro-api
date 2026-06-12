"""Structured audit logging utilities.

This module provides:
1) ``log_audit_event``: canonical helper for writing audit records.
2) ``log_audit``: compatibility wrapper for legacy call sites.
3) ``audit_log``: decorator for low-friction endpoint instrumentation.

Writes are fire-and-forget to avoid API latency impact.
"""
import copy
import logging
import threading
import uuid
from datetime import datetime
from functools import wraps

from flask import request

from smart_invoice_pro.utils.cosmos_client import audit_logs_container
from smart_invoice_pro.utils.response_sanitizer import sanitize_item

logger = logging.getLogger(__name__)

_WRITE_STATS = {"attempted": 0, "succeeded": 0, "failed": 0}

_AUDIT_SENSITIVE = {
    "password",
    "portal_password",
    "portal_token",
    "token",
    "refresh_token",
    "hashed_pw",
    "secret",
    "api_key",
    "access_token",
    "authorization",
}

# Entity → Activity Center category
_ENTITY_CATEGORY = {
    "invoice": "financial",
    "payment": "financial",
    "quote": "financial",
    "bill": "financial",
    "expense": "financial",
    "purchase_order": "financial",
    "sales_order": "financial",
    "customer": "financial",
    "vendor": "financial",
    "product": "financial",
    "recurring_profile": "financial",
    "bank_import_batch": "banking",
    "bank_import_row": "banking",
    "bank_account": "banking",
    "bank_transaction": "banking",
    "auth": "security",
    "user": "security",
    "session": "security",
    "role": "security",
    "branding": "settings",
    "tax_rate": "settings",
    "organization_profile": "settings",
    "invoice_preferences": "settings",
    "settings": "settings",
    "feature_flags": "settings",
    "tenant": "system",
    "workflow": "workflow",
}

# (entity, action) → risk level; action-only fallback via _ACTION_RISK
_ACTION_RISK = {
    "DELETE": "high",
    "VOID": "high",
    "ENTITY_DELETED": "high",
    "RESET_PASSWORD": "high",
    "LOGIN_FAILED": "medium",
    "CONVERTED": "medium",
    "MERGE": "high",
    "SOFT_DELETE": "high",
    "UPDATE_STATUS": "medium",
    "PAYMENT_RECORDED": "medium",
    "INVOICE_SENT": "low",
    "APPROVAL_SUBMITTED": "medium",
    "APPROVAL_COMPLETED": "medium",
    "APPROVAL_REJECTED": "high",
    "BANK_ACCOUNT_DELETED": "high",
    "BANK_IMPORT_FAILED": "high",
    "RECONCILIATION_UNMATCHED": "medium",
    "BANK_TRANSACTION_DELETED": "medium",
    "BANK_IMPORT_BATCH_DELETED": "medium",
}
_ENTITY_ACTION_RISK = {
    ("user", "DELETE"): "high",
    ("user", "UPDATE"): "high",
    ("role", "UPDATE"): "high",
    ("role", "CREATE"): "high",
    ("role", "DELETE"): "high",
    ("tax_rate", "CREATE"): "high",
    ("tax_rate", "UPDATE"): "high",
    ("tax_rate", "DELETE"): "high",
    ("branding", "UPDATE"): "high",
    ("organization_profile", "UPDATE"): "high",
    ("invoice_preferences", "UPDATE"): "medium",
    ("auth", "LOGIN"): "low",
    ("auth", "LOGOUT"): "low",
    ("auth", "LOGIN_FAILED"): "medium",
}

_LABEL_KEYS = (
    "invoice_number", "quote_number", "bill_number", "po_number", "so_number",
    "purchase_order_number", "sales_order_number", "expense_number",
    "name", "username", "title", "filename", "account_name", "bank_name",
    "description",
)


def _normalize_action(action):
    return str(action or "").strip().upper()


def _resolve_category(entity: str) -> str:
    return _ENTITY_CATEGORY.get(str(entity or "").lower(), "system")


def _resolve_risk_level(entity: str, action: str) -> str:
    entity_l = str(entity or "").lower()
    action_u = _normalize_action(action)
    if (entity_l, action_u) in _ENTITY_ACTION_RISK:
        return _ENTITY_ACTION_RISK[(entity_l, action_u)]
    if action_u in _ACTION_RISK:
        return _ACTION_RISK[action_u]
    if action_u in ("CREATE", "UPDATE", "IMPORT", "ENTITY_ARCHIVED", "ENTITY_RESTORED"):
        return "medium"
    if action_u in ("LOGIN", "LOGOUT", "VIEW"):
        return "low"
    return "low"


def _infer_entity_label(entity: str, before, after) -> str | None:
    doc = after if isinstance(after, dict) else {}
    if not doc and isinstance(before, dict):
        doc = before
    for key in _LABEL_KEYS:
        val = doc.get(key)
        if val:
            return str(val)
    return None


def _build_summary(entity: str, action: str, entity_label: str | None) -> str:
    entity_l = str(entity or "record").replace("_", " ")
    action_u = _normalize_action(action)
    label = entity_label or entity_l
    verbs = {
        "CREATE": "created",
        "UPDATE": "updated",
        "DELETE": "deleted",
        "VOID": "voided",
        "LOGIN": "logged in",
        "LOGOUT": "logged out",
        "LOGIN_FAILED": "failed login attempt",
        "CONVERTED": "converted",
        "PAYMENT_RECORDED": "payment recorded",
        "INVOICE_SENT": "sent to customer",
        "APPROVAL_SUBMITTED": "submitted for approval",
        "APPROVAL_COMPLETED": "approved",
        "APPROVAL_REJECTED": "rejected",
        "MERGE": "merged",
        "ENTITY_ARCHIVED": "archived",
        "ENTITY_RESTORED": "restored",
        "ENTITY_DELETED": "permanently deleted",
        "BANK_ACCOUNT_CREATED": "connected",
        "BANK_ACCOUNT_UPDATED": "updated",
        "BANK_ACCOUNT_DELETED": "disconnected",
        "BANK_IMPORT_BATCH_CREATED": "import started",
        "BANK_IMPORT_BATCH_APPROVED": "import approved",
        "BANK_IMPORT_COMPLETED": "import completed",
        "BANK_IMPORT_FAILED": "import failed",
        "BANK_IMPORT_ROW_UPDATED": "import row updated",
        "BANK_IMPORT_BATCH_DELETED": "import batch deleted",
        "BANK_STATEMENT_IMPORTED": "statement imported",
        "RECONCILIATION_MATCHED": "reconciled",
        "RECONCILIATION_UNMATCHED": "unmatched",
        "RECONCILIATION_OVERRIDE": "expense created from transaction",
        "BANK_AUTO_MATCH_RUN": "auto-match run",
        "BANK_AI_MATCH_RUN": "AI match run",
        "BANK_TRANSACTION_DELETED": "transaction removed",
    }
    verb = verbs.get(action_u, action_u.lower().replace("_", " "))
    return f"{label} {verb}".strip()


def _safe_request_attr(name, default=None):
    try:
        return getattr(request, name, default)
    except RuntimeError:
        return default


def _lookup_user_actor(user_id: str) -> dict:
    """Best-effort user enrichment for audit display fields."""
    if not user_id or user_id == "cron":
        return {"user_name": "System", "user_email": None}
    try:
        from smart_invoice_pro.utils.cosmos_client import users_container
        items = list(users_container.query_items(
            query="SELECT c.email, c.name, c.username FROM c WHERE c.id = @uid",
            parameters=[{"name": "@uid", "value": user_id}],
            enable_cross_partition_query=True,
        ))
        if items:
            row = items[0]
            return {
                "user_email": row.get("email") or row.get("username"),
                "user_name": row.get("name") or row.get("username"),
            }
    except Exception as exc:
        logger.debug("[audit] user lookup failed for %s: %s", user_id, exc)
    return {}


def _extract_actor(data):
    user_id = data.get("user_id") or _safe_request_attr("user_id")
    user_email = data.get("user_email") or _safe_request_attr("user_email")
    user_name = data.get("user_name")

    if user_id and not user_email and not user_name:
        profile = _lookup_user_actor(user_id)
        user_email = user_email or profile.get("user_email")
        user_name = user_name or profile.get("user_name")

    return {
        "tenant_id": data.get("tenant_id") or _safe_request_attr("tenant_id"),
        "user_id": user_id,
        "user_email": user_email,
        "user_name": user_name,
    }


def _extract_request_meta(data):
    try:
        headers = request.headers
        ip_address = data.get("ip_address") or request.headers.get("X-Forwarded-For", request.remote_addr)
        user_agent = data.get("user_agent") or headers.get("User-Agent")
    except RuntimeError:
        ip_address = data.get("ip_address")
        user_agent = data.get("user_agent")
    return {
        "ip_address": ip_address,
        "user_agent": user_agent,
    }


def _deep_clean(value):
    if value is None:
        return None
    if isinstance(value, dict):
        cleaned = sanitize_item(value, additional_sensitive_fields=_AUDIT_SENSITIVE)
        return {k: _deep_clean(v) for k, v in cleaned.items()}
    if isinstance(value, list):
        return [_deep_clean(v) for v in value]
    return value


def get_audit_write_stats():
    """In-process counters for audit write health monitoring."""
    return dict(_WRITE_STATS)


def _write_audit_doc(doc):
    _WRITE_STATS["attempted"] += 1
    try:
        audit_logs_container.create_item(body=doc)
        _WRITE_STATS["succeeded"] += 1
    except Exception as exc:
        _WRITE_STATS["failed"] += 1
        logger.warning("[audit] Failed to write audit log: %s", exc)


def _fire_and_forget_write(doc):
    t = threading.Thread(target=_write_audit_doc, args=(doc,), daemon=True)
    t.start()


def log_audit_event(data):
    """Write a structured audit log entry.

    Expected keys in ``data``
    - action, entity, entity_id, before, after, metadata
    Optional auto-populated from request context when absent:
    - tenant_id, user_id, user_email, user_name, ip_address, user_agent
    Optional explicit enrichment:
    - category, risk_level, entity_label, summary, module
    """
    if not isinstance(data, dict):
        return

    actor = _extract_actor(data)
    if not actor["tenant_id"]:
        return

    req_meta = _extract_request_meta(data)
    now = datetime.utcnow().isoformat()

    entity = str(data.get("entity") or "").strip().lower() or "unknown"
    action = _normalize_action(data.get("action"))

    before = _deep_clean(copy.deepcopy(data.get("before")))
    after = _deep_clean(copy.deepcopy(data.get("after")))
    metadata = _deep_clean(copy.deepcopy(data.get("metadata"))) or {}

    entity_label = data.get("entity_label") or _infer_entity_label(entity, before, after)
    category = data.get("category") or _resolve_category(entity)
    risk_level = data.get("risk_level") or _resolve_risk_level(entity, action)
    summary = data.get("summary") or _build_summary(entity, action, entity_label)
    module = data.get("module") or entity

    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": actor["tenant_id"],
        "user_id": actor["user_id"],
        "user_email": actor["user_email"],
        "user_name": actor.get("user_name"),
        "action": action,
        "entity": entity,
        "entity_id": str(data.get("entity_id")) if data.get("entity_id") is not None else None,
        "entity_label": entity_label,
        "module": module,
        "category": category,
        "risk_level": risk_level,
        "summary": summary,
        "before": before,
        "after": after,
        "metadata": metadata,
        "ip_address": req_meta["ip_address"],
        "user_agent": req_meta["user_agent"],
        "created_at": now,
        "immutable": True,
        # Backward-compatible aliases for existing consumers
        "entity_type": entity,
        "changes": {"before": before, "after": after},
        "timestamp": now,
    }

    _fire_and_forget_write(doc)


def log_audit(
    entity_type,
    action,
    entity_id,
    before,
    after,
    *,
    user_id=None,
    tenant_id=None,
    metadata=None,
    entity_label=None,
    summary=None,
):
    """Backward-compatible wrapper used by existing APIs."""
    log_audit_event(
        {
            "entity": entity_type,
            "action": action,
            "entity_id": entity_id,
            "before": before,
            "after": after,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "metadata": metadata,
            "entity_label": entity_label,
            "summary": summary,
        }
    )


def audit_log(action, entity):
    """Decorator to record request/response snapshots for endpoint calls."""
    def _decorator(func):
        @wraps(func)
        def _wrapped(*args, **kwargs):
            req_payload = request.get_json(silent=True) if request else None
            response = func(*args, **kwargs)

            status_code = 200
            resp_payload = None
            try:
                if isinstance(response, tuple):
                    resp_obj = response[0]
                    status_code = response[1] if len(response) > 1 else 200
                else:
                    resp_obj = response
                if hasattr(resp_obj, "get_json"):
                    resp_payload = resp_obj.get_json(silent=True)
            except Exception:
                resp_payload = None

            if status_code < 400:
                log_audit_event(
                    {
                        "action": action,
                        "entity": entity,
                        "entity_id": kwargs.get("id") or kwargs.get("invoice_id") or kwargs.get("customer_id") or kwargs.get("product_id") or kwargs.get("vendor_id"),
                        "before": None,
                        "after": resp_payload,
                        "metadata": {
                            "method": request.method,
                            "path": request.path,
                            "request": _deep_clean(req_payload),
                        },
                    }
                )

            return response

        return _wrapped

    return _decorator


def log_bulk_archive_summary(*, tenant_id, user_id, entity_type, requested_count, success_count, failed_count, dependency_summary=None):
    """Record an audit-safe summary for lifecycle bulk archive operations."""
    log_audit_event(
        {
            "action": "BULK_ARCHIVE_COMPLETED",
            "entity": entity_type,
            "entity_id": None,
            "before": None,
            "after": {
                "requested_count": requested_count,
                "success_count": success_count,
                "failed_count": failed_count,
            },
            "metadata": {
                "event": "bulk_archive_completed",
                "dependency_summary": dependency_summary or {},
            },
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
    )

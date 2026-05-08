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


def _normalize_action(action):
    return str(action or "").strip().upper()


def _safe_request_attr(name, default=None):
    try:
        return getattr(request, name, default)
    except RuntimeError:
        return default


def _extract_actor(data):
    return {
        "tenant_id": data.get("tenant_id") or _safe_request_attr("tenant_id"),
        "user_id": data.get("user_id") or _safe_request_attr("user_id"),
        "user_email": data.get("user_email") or _safe_request_attr("user_email"),
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


def _write_audit_doc(doc):
    try:
        audit_logs_container.create_item(body=doc)
    except Exception as exc:
        logger.warning("[audit] Failed to write audit log: %s", exc)


def _fire_and_forget_write(doc):
    t = threading.Thread(target=_write_audit_doc, args=(doc,), daemon=True)
    t.start()


def log_audit_event(data):
    """Write a structured audit log entry.

    Expected keys in ``data``
    - action, entity, entity_id, before, after, metadata
    Optional auto-populated from request context when absent:
    - tenant_id, user_id, user_email, ip_address, user_agent
    """
    if not isinstance(data, dict):
        return

    actor = _extract_actor(data)
    if not actor["tenant_id"]:
        return

    req_meta = _extract_request_meta(data)
    now = datetime.utcnow().isoformat()

    before = _deep_clean(copy.deepcopy(data.get("before")))
    after = _deep_clean(copy.deepcopy(data.get("after")))
    metadata = _deep_clean(copy.deepcopy(data.get("metadata")))

    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": actor["tenant_id"],
        "user_id": actor["user_id"],
        "user_email": actor["user_email"],
        "action": _normalize_action(data.get("action")),
        "entity": str(data.get("entity") or "").strip().lower() or "unknown",
        "entity_id": str(data.get("entity_id")) if data.get("entity_id") is not None else None,
        "before": before,
        "after": after,
        "metadata": metadata,
        "ip_address": req_meta["ip_address"],
        "user_agent": req_meta["user_agent"],
        "created_at": now,
        # Backward-compatible aliases for existing consumers
        "entity_type": str(data.get("entity") or "").strip().lower() or "unknown",
        "changes": {"before": before, "after": after},
        "timestamp": now,
    }

    _fire_and_forget_write(doc)


def log_audit(entity_type, action, entity_id, before, after, *, user_id=None, tenant_id=None):
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

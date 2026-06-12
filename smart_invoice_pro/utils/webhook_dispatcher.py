"""
Webhook dispatcher utility.

Usage (call from any API handler after a notable event):

    from smart_invoice_pro.utils.webhook_dispatcher import dispatch_webhook_event

    dispatch_webhook_event(
        tenant_id="<tenant>",
        event="invoice.created",
        payload={"invoice_id": "...", "amount": 1000, ...}
    )

The function fires-and-forgets: it runs the HTTP calls in a background
thread so that the originating request is never delayed or failed by a
slow/unavailable webhook endpoint.

Each webhook endpoint has its own optional `secret` field. If set, that
per-endpoint secret is used for HMAC-SHA256 signing; otherwise no signature
header is sent. This decouples outbound webhook signing from the inbound
payment webhook verification secret.

Every delivery attempt (success or failure) is written to the
`webhook_logs` Cosmos container so tenants have delivery visibility.
"""
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from datetime import datetime

import requests

from smart_invoice_pro.utils.cosmos_client import settings_container, webhook_logs_container

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10   # seconds per webhook call
_MAX_RETRIES     = 2    # simple retry count on connection errors


def _get_webhooks_for_tenant(tenant_id: str) -> list[dict]:
    """Return the list of active webhook entries for this tenant."""
    doc_id = f"{tenant_id}:integrations_settings"
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return []
    return [wh for wh in items[0].get("webhooks", []) if wh.get("active")]


def _sign_payload(secret: str, body: bytes) -> str:
    """Return HMAC-SHA256 hex signature for the payload."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _write_log(tenant_id: str, webhook_id: str, event: str,
               url: str, status_code: int | None,
               success: bool, error: str | None) -> None:
    """Persist a delivery attempt to webhook_logs. Non-blocking best-effort."""
    try:
        webhook_logs_container.create_item(body={
            "id":           str(uuid.uuid4()),
            "tenant_id":    tenant_id,
            "webhook_id":   webhook_id,
            "event":        event,
            "url":          url,
            "status_code":  status_code,
            "success":      success,
            "error":        error,
            "delivered_at": datetime.utcnow().isoformat(),
        })
    except Exception as log_exc:
        logger.warning("[webhook] failed to write delivery log: %s", log_exc)


def _deliver(tenant_id: str, webhook_id: str, url: str,
             event: str, payload: dict, secret: str | None) -> None:
    """Send one webhook HTTP POST (with retries) and log the outcome."""
    envelope = {
        "id":         str(uuid.uuid4()),
        "event":      event,
        "created_at": datetime.utcnow().isoformat(),
        "data":       payload,
    }
    body = json.dumps(envelope, ensure_ascii=False).encode()
    headers = {
        "Content-Type":         "application/json",
        "X-SmartInvoice-Event": event,
    }
    if secret:
        headers["X-SmartInvoice-Signature"] = _sign_payload(secret, body)

    last_exc: str | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(url, data=body, headers=headers,
                                 timeout=_REQUEST_TIMEOUT)
            logger.info("[webhook] %s → %s  status=%s", event, url, resp.status_code)
            _write_log(tenant_id, webhook_id, event, url,
                       resp.status_code, resp.ok, None if resp.ok else f"HTTP {resp.status_code}")
            return
        except requests.RequestException as exc:
            last_exc = str(exc)
            logger.warning("[webhook] attempt %d/%d failed for %s: %s",
                           attempt, _MAX_RETRIES, url, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(1)

    _write_log(tenant_id, webhook_id, event, url, None, False, last_exc)


def _fire(tenant_id: str, event: str, payload: dict) -> None:
    """Worker function executed in a daemon thread."""
    try:
        webhooks = _get_webhooks_for_tenant(tenant_id)
        if not webhooks:
            return
        for wh in webhooks:
            if event in wh.get("events", []):
                # Use per-endpoint secret; fall back to None (no signature)
                secret = wh.get("secret") or None
                _deliver(tenant_id, wh.get("id", ""), wh["url"], event, payload, secret)
    except Exception as exc:
        logger.error("[webhook] dispatcher error: %s", exc)


def dispatch_webhook_event(tenant_id: str, event: str, payload: dict) -> None:
    """
    Fire webhooks for *event* in a background thread.
    Returns immediately — does not block the caller.
    """
    t = threading.Thread(target=_fire, args=(tenant_id, event, payload), daemon=True)
    t.start()

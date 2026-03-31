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

Supported events (must match SUPPORTED_EVENTS in integrations_settings_api):
  - invoice.created
  - invoice.paid
  - customer.created
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

from smart_invoice_pro.utils.cosmos_client import settings_container

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


def _get_webhook_secret(tenant_id: str) -> str | None:
    """Return the stored payment webhook_secret (used for signing)."""
    doc_id = f"{tenant_id}:integrations_settings"
    items = list(settings_container.query_items(
        query="SELECT c.payments.webhook_secret FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": doc_id}],
        enable_cross_partition_query=True,
    ))
    if items:
        return items[0].get("webhook_secret")
    return None


def _sign_payload(secret: str, body: bytes) -> str:
    """Return HMAC-SHA256 hex signature for the payload."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deliver(url: str, event: str, payload: dict, secret: str | None) -> None:
    """Send one webhook HTTP POST (with retries)."""
    envelope = {
        "id":           str(uuid.uuid4()),
        "event":        event,
        "created_at":   datetime.utcnow().isoformat(),
        "data":         payload,
    }
    body = json.dumps(envelope, ensure_ascii=False).encode()
    headers = {
        "Content-Type":        "application/json",
        "X-SmartInvoice-Event": event,
    }
    if secret:
        headers["X-SmartInvoice-Signature"] = _sign_payload(secret, body)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(url, data=body, headers=headers,
                                 timeout=_REQUEST_TIMEOUT)
            logger.info("[webhook] %s → %s  status=%s", event, url, resp.status_code)
            return
        except requests.RequestException as exc:
            logger.warning("[webhook] attempt %d/%d failed for %s: %s",
                           attempt, _MAX_RETRIES, url, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(1)


def _fire(tenant_id: str, event: str, payload: dict) -> None:
    """Worker function executed in a daemon thread."""
    try:
        webhooks = _get_webhooks_for_tenant(tenant_id)
        if not webhooks:
            return
        secret = _get_webhook_secret(tenant_id)
        for wh in webhooks:
            if event in wh.get("events", []):
                _deliver(wh["url"], event, payload, secret)
    except Exception as exc:
        logger.error("[webhook] dispatcher error: %s", exc)


def dispatch_webhook_event(tenant_id: str, event: str, payload: dict) -> None:
    """
    Fire webhooks for *event* in a background thread.
    Returns immediately — does not block the caller.
    """
    t = threading.Thread(target=_fire, args=(tenant_id, event, payload), daemon=True)
    t.start()

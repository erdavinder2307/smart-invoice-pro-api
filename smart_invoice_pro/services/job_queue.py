"""
job_queue.py
============
Redis-backed background job queue foundation for Solidev Books.

This module provides the INFRASTRUCTURE only. No existing jobs have been
migrated here yet. It is designed to be adopted incrementally.

Architecture
------------
Jobs are stored as JSON objects in Redis lists (one list per queue name).
Producers call push_job(); consumers pop and process with pop_job().

Queue names
-----------
  "emails"     – transactional emails (reminders, confirmations, alerts)
  "reports"    – background report generation
  "demos"      – demo environment reset + seeding
  "webhooks"   – outbound webhook delivery with retries
  "alerts"     – low-stock, overdue, and system alerts

Degradation
-----------
If Redis is unavailable, push_job() is a no-op and pop_job() returns None.
Jobs must be idempotent; duplicate delivery is possible after a crash.

Future migration path
---------------------
1. emails:     Replace synchronous mail calls in reminder_job.py and
               email_template_service.py with push_job("emails", {...})
               and create a worker that calls the Azure Communication API.

2. reminders:  Replace APScheduler process_payment_reminders() with a
               push_job("emails", {"type": "payment_reminder", ...}) call
               and let the email worker handle dispatch.

3. reports:    Heavy aggregation (P&L, cash flow) can be off-loaded by
               enqueuing a job, storing the result in Redis, and polling
               from the frontend.

4. demo reset: push_job("demos", {"action": "reset", "tenant_id": ...})
               triggers a controlled seed-data reload for demo tenants.

5. webhooks:   Replace the current synchronous webhook dispatches with
               push_job("webhooks", {"url": ..., "payload": ..., "attempt": 1})
               and a dedicated retry worker.

Usage example
-------------
    from smart_invoice_pro.services.job_queue import push_job

    push_job("emails", {
        "type": "payment_reminder",
        "invoice_id": "abc-123",
        "tenant_id": "tenant-xyz",
        "recipient_email": "customer@example.com",
    })

Worker skeleton (not yet wired)
---------------------------------
    from smart_invoice_pro.services.job_queue import pop_job

    while True:
        job = pop_job("emails", timeout=5)  # blocking pop with 5s timeout
        if job:
            dispatch_email(job)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from smart_invoice_pro.utils.redis_client import cache

logger = logging.getLogger(__name__)

# Redis key prefix for all queues
_QUEUE_PREFIX = "job"

# Maximum number of jobs kept in a dead-letter queue per queue name
_DLQ_MAX_LENGTH = 1000


def _queue_key(queue_name: str) -> str:
    return f"{_QUEUE_PREFIX}:{queue_name}"


def _dlq_key(queue_name: str) -> str:
    return f"{_QUEUE_PREFIX}:{queue_name}:dlq"


def push_job(queue_name: str, payload: dict[str, Any]) -> bool:
    """
    Push a job onto the named queue.

    Returns True if the job was enqueued, False if Redis is unavailable.

    Parameters
    ----------
    queue_name : str
        Name of the queue (e.g. "emails", "reports").
    payload : dict
        Arbitrary JSON-serialisable job data.  Include a ``type`` key so
        consumers know how to handle the job.
    """
    if not cache.available:
        logger.debug("job_queue: Redis unavailable — job not enqueued (queue=%s)", queue_name)
        return False
    try:
        client = cache.get_raw_client()
        client.rpush(_queue_key(queue_name), json.dumps(payload, default=str))
        return True
    except Exception as exc:
        logger.warning("job_queue: push_job failed (queue=%s): %s", queue_name, exc)
        return False


def pop_job(queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
    """
    Pop the next job from the named queue (blocking).

    Parameters
    ----------
    queue_name : str
        Name of the queue.
    timeout : int
        Seconds to block waiting for a job. 0 = non-blocking.

    Returns
    -------
    dict or None
        The job payload, or None if the queue is empty or Redis is unavailable.
    """
    if not cache.available:
        return None
    try:
        client = cache.get_raw_client()
        if timeout > 0:
            result = client.blpop(_queue_key(queue_name), timeout=timeout)
            if result:
                _, raw = result
                return json.loads(raw)
        else:
            raw = client.lpop(_queue_key(queue_name))
            if raw:
                return json.loads(raw)
    except Exception as exc:
        logger.warning("job_queue: pop_job failed (queue=%s): %s", queue_name, exc)
    return None


def queue_length(queue_name: str) -> int:
    """Return the number of pending jobs in the queue. Returns 0 on error."""
    if not cache.available:
        return 0
    try:
        client = cache.get_raw_client()
        return int(client.llen(_queue_key(queue_name)))
    except Exception as exc:
        logger.warning("job_queue: queue_length failed (queue=%s): %s", queue_name, exc)
        return 0


def move_to_dlq(queue_name: str, job: dict[str, Any]) -> None:
    """
    Move a failed job to the dead-letter queue for the given queue name.
    The DLQ is capped at _DLQ_MAX_LENGTH entries (oldest are evicted).
    """
    if not cache.available:
        return
    try:
        client = cache.get_raw_client()
        dlq = _dlq_key(queue_name)
        client.rpush(dlq, json.dumps(job, default=str))
        # Trim to keep only the most recent failures
        client.ltrim(dlq, -_DLQ_MAX_LENGTH, -1)
    except Exception as exc:
        logger.warning("job_queue: move_to_dlq failed (queue=%s): %s", queue_name, exc)

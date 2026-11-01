"""
redis_client.py
===============
Thin Redis wrapper with graceful degradation for Solidev Books.

If Redis is unavailable (REDIS_URL not set, or connection failure) every
operation silently returns None / does nothing, so the rest of the app
continues to work via Cosmos DB reads.

Key prefixes used across the platform
--------------------------------------
  permissions:{tenant_id}:{user_id}             TTL 300s  (permission cache)
  dashboard:{tenant_id}:{range}:{s}:{e}         TTL 300s  (dashboard summary)
  rate_limit:*                                   managed by Flask-Limiter
  job:{queue_name}                               no TTL    (background job queue)

Usage
-----
    from smart_invoice_pro.utils.redis_client import cache

    # Store a value
    cache.set("my-key", {"some": "data"}, ttl=300)

    # Read a value (returns None on miss or error)
    data = cache.get("my-key")

    # Delete
    cache.delete("my-key")

    # Delete all keys matching a glob pattern
    cache.delete_pattern("dashboard:tenant-abc:*")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── TTL constants ──────────────────────────────────────────────────────────────

PERMISSION_CACHE_TTL = int(os.getenv("REDIS_PERMISSION_TTL", "300"))   # 5 min
DASHBOARD_CACHE_TTL  = int(os.getenv("REDIS_DASHBOARD_TTL",  "300"))   # 5 min


class RedisClient:
    """
    Lazy-initialised Redis wrapper.  All public methods swallow exceptions
    so the application never crashes due to a Redis issue.
    """

    def __init__(self) -> None:
        self._client = None
        self._available = False
        self._initialised = False

    def _init(self) -> None:
        if self._initialised:
            return
        self._initialised = True

        url = (os.getenv("REDIS_URL") or os.getenv("REDIS_CONNECTION_STRING") or "").strip()
        if not url:
            logger.debug("REDIS_URL not set — Redis caching disabled.")
            return

        try:
            import redis  # imported here so the module loads even without the package

            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            self._client = client
            self._available = True
            safe_url = url.split("@")[-1] if "@" in url else url
            logger.info("Redis connected: %s", safe_url)
        except ImportError:
            logger.warning(
                "redis package not installed — run: pip install redis>=5.0.0"
            )
        except Exception as exc:
            logger.warning("Redis unavailable — caching disabled: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        self._init()
        return self._available and self._client is not None

    def get(self, key: str) -> Any:
        """Return the cached value for *key*, or None on miss / error."""
        if not self.available:
            return None
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis GET error key=%s: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Serialise *value* as JSON and store with TTL in seconds."""
        if not self.available:
            return
        try:
            self._client.setex(key, ttl, json.dumps(value, default=str))
        except Exception as exc:
            logger.warning("Redis SET error key=%s: %s", key, exc)

    def delete(self, key: str) -> None:
        """Delete a single key."""
        if not self.available:
            return
        try:
            self._client.delete(key)
        except Exception as exc:
            logger.warning("Redis DEL error key=%s: %s", key, exc)

    def delete_pattern(self, pattern: str) -> None:
        """
        Delete all keys matching a glob *pattern*.

        Use sparingly — KEYS is O(N) and blocks Redis during execution.
        Acceptable for low-cardinality admin operations (role changes, tenant resets).
        """
        if not self.available:
            return
        try:
            keys = self._client.keys(pattern)
            if keys:
                self._client.delete(*keys)
        except Exception as exc:
            logger.warning("Redis DEL pattern error pattern=%s: %s", pattern, exc)

    def incr(self, key: str, ttl: int) -> int:
        """
        Atomic increment. Sets *ttl* on the key the first time it is created.
        Returns 0 if Redis is unavailable (callers treat 0 as "no limit data").
        """
        if not self.available:
            return 0
        try:
            pipe = self._client.pipeline(True)
            pipe.incr(key)
            pipe.expire(key, ttl)
            result = pipe.execute()
            return int(result[0])
        except Exception as exc:
            logger.warning("Redis INCR error key=%s: %s", key, exc)
            return 0

    def get_raw_client(self):
        """
        Return the underlying redis.Redis instance for Flask-Limiter.
        Returns None when Redis is unavailable.
        """
        self._init()
        return self._client


# ── Module-level singleton ────────────────────────────────────────────────────

#: Use ``from smart_invoice_pro.utils.redis_client import cache`` everywhere.
cache = RedisClient()


# ── Convenience key builders ──────────────────────────────────────────────────

def permission_cache_key(tenant_id: str, user_id: str) -> str:
    return f"permissions:{tenant_id}:{user_id}"


def dashboard_cache_key(tenant_id: str, range_type: str, start: str, end: str) -> str:
    return f"dashboard:{tenant_id}:{range_type}:{start}:{end}"

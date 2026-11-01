"""
Unit tests for smart_invoice_pro/utils/redis_client.py

Redis is never required to be running during tests.
All tests use a mocked redis.Redis instance to exercise the
RedisClient wrapper's cache, fallback, and error-handling paths.
"""
import json
from unittest.mock import MagicMock, patch, call

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_client(available=True):
    """
    Return a fresh RedisClient instance backed by a MagicMock redis.Redis.
    Setting available=False simulates 'no REDIS_URL configured'.
    """
    from smart_invoice_pro.utils.redis_client import RedisClient

    rc = RedisClient()
    if available:
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        rc._client = mock_redis
        rc._available = True
    rc._initialised = True  # skip _init() in tests
    return rc, (rc._client if available else None)


# ─────────────────────────────────────────────────────────────────────────────
# available property
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailable:
    def test_true_when_client_set(self):
        rc, _ = _make_client(available=True)
        assert rc.available is True

    def test_false_when_no_client(self):
        rc, _ = _make_client(available=False)
        assert rc.available is False


# ─────────────────────────────────────────────────────────────────────────────
# get()
# ─────────────────────────────────────────────────────────────────────────────

class TestGet:
    def test_returns_none_when_unavailable(self):
        rc, _ = _make_client(available=False)
        assert rc.get("any-key") is None

    def test_returns_none_on_cache_miss(self):
        rc, mock = _make_client()
        mock.get.return_value = None
        assert rc.get("missing") is None

    def test_deserialises_json_on_hit(self):
        rc, mock = _make_client()
        mock.get.return_value = json.dumps({"is_admin": True, "permissions": {}})
        result = rc.get("some-key")
        assert result == {"is_admin": True, "permissions": {}}

    def test_returns_none_on_redis_error(self):
        rc, mock = _make_client()
        mock.get.side_effect = Exception("connection refused")
        assert rc.get("bad-key") is None


# ─────────────────────────────────────────────────────────────────────────────
# set()
# ─────────────────────────────────────────────────────────────────────────────

class TestSet:
    def test_no_op_when_unavailable(self):
        rc, _ = _make_client(available=False)
        # Should not raise
        rc.set("k", {"v": 1})

    def test_calls_setex_with_ttl(self):
        rc, mock = _make_client()
        rc.set("mykey", {"x": 1}, ttl=120)
        mock.setex.assert_called_once_with("mykey", 120, json.dumps({"x": 1}))

    def test_silences_redis_error(self):
        rc, mock = _make_client()
        mock.setex.side_effect = Exception("write error")
        rc.set("k", {"v": 1})  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# delete()
# ─────────────────────────────────────────────────────────────────────────────

class TestDelete:
    def test_no_op_when_unavailable(self):
        rc, _ = _make_client(available=False)
        rc.delete("k")  # must not raise

    def test_calls_delete(self):
        rc, mock = _make_client()
        rc.delete("target-key")
        mock.delete.assert_called_once_with("target-key")

    def test_silences_redis_error(self):
        rc, mock = _make_client()
        mock.delete.side_effect = Exception("del error")
        rc.delete("k")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# delete_pattern()
# ─────────────────────────────────────────────────────────────────────────────

class TestDeletePattern:
    def test_no_op_when_unavailable(self):
        rc, _ = _make_client(available=False)
        rc.delete_pattern("permissions:*")  # must not raise

    def test_deletes_matching_keys(self):
        rc, mock = _make_client()
        mock.keys.return_value = ["permissions:t1:u1", "permissions:t1:u2"]
        rc.delete_pattern("permissions:t1:*")
        mock.delete.assert_called_once_with("permissions:t1:u1", "permissions:t1:u2")

    def test_no_delete_when_no_keys_match(self):
        rc, mock = _make_client()
        mock.keys.return_value = []
        rc.delete_pattern("permissions:empty:*")
        mock.delete.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# incr()
# ─────────────────────────────────────────────────────────────────────────────

class TestIncr:
    def test_returns_zero_when_unavailable(self):
        rc, _ = _make_client(available=False)
        assert rc.incr("counter", 60) == 0

    def test_returns_incremented_value(self):
        rc, mock = _make_client()
        pipe = MagicMock()
        pipe.execute.return_value = [3, True]
        mock.pipeline.return_value = pipe
        assert rc.incr("rate:ip:1.2.3.4", 60) == 3
        pipe.incr.assert_called_once_with("rate:ip:1.2.3.4")
        pipe.expire.assert_called_once_with("rate:ip:1.2.3.4", 60)


# ─────────────────────────────────────────────────────────────────────────────
# Key builder helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyBuilders:
    def test_permission_cache_key_format(self):
        from smart_invoice_pro.utils.redis_client import permission_cache_key
        assert permission_cache_key("t1", "u1") == "permissions:t1:u1"

    def test_dashboard_cache_key_format(self):
        from smart_invoice_pro.utils.redis_client import dashboard_cache_key
        k = dashboard_cache_key("t1", "this_month", "2026-06-01", "2026-06-22")
        assert k == "dashboard:t1:this_month:2026-06-01:2026-06-22"

    def test_dashboard_cache_key_empty_dates(self):
        from smart_invoice_pro.utils.redis_client import dashboard_cache_key
        k = dashboard_cache_key("t1", "this_year", "", "")
        assert k == "dashboard:t1:this_year::"

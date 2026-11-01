"""
Unit tests for smart_invoice_pro/services/job_queue.py

All tests use a mocked redis client so Redis is never required to be running.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_available_cache(pipeline_return=None):
    """Return a mock cache object simulating Redis being available."""
    from smart_invoice_pro.utils.redis_client import RedisClient

    rc = RedisClient()
    mock_redis = MagicMock()
    rc._client = mock_redis
    rc._available = True
    rc._initialised = True
    return rc, mock_redis


def _unavailable_cache():
    from smart_invoice_pro.utils.redis_client import RedisClient

    rc = RedisClient()
    rc._available = False
    rc._initialised = True
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# push_job
# ─────────────────────────────────────────────────────────────────────────────

class TestPushJob:
    def test_returns_false_when_redis_unavailable(self):
        dead_cache = _unavailable_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", dead_cache):
            from smart_invoice_pro.services.job_queue import push_job
            result = push_job("emails", {"type": "reminder"})
        assert result is False

    def test_pushes_json_to_redis_list(self):
        live_cache, mock_redis = _make_available_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import push_job
            result = push_job("emails", {"type": "reminder", "invoice_id": "inv-1"})
        assert result is True
        mock_redis.rpush.assert_called_once()
        args = mock_redis.rpush.call_args[0]
        assert args[0] == "job:emails"
        payload = json.loads(args[1])
        assert payload["type"] == "reminder"
        assert payload["invoice_id"] == "inv-1"

    def test_returns_false_on_redis_error(self):
        live_cache, mock_redis = _make_available_cache()
        mock_redis.rpush.side_effect = Exception("write error")
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import push_job
            result = push_job("emails", {"type": "reminder"})
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# pop_job
# ─────────────────────────────────────────────────────────────────────────────

class TestPopJob:
    def test_returns_none_when_redis_unavailable(self):
        dead_cache = _unavailable_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", dead_cache):
            from smart_invoice_pro.services.job_queue import pop_job
            result = pop_job("emails")
        assert result is None

    def test_returns_none_on_empty_queue(self):
        live_cache, mock_redis = _make_available_cache()
        mock_redis.lpop.return_value = None
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import pop_job
            result = pop_job("emails", timeout=0)
        assert result is None

    def test_pops_and_deserialises_job(self):
        live_cache, mock_redis = _make_available_cache()
        mock_redis.lpop.return_value = json.dumps({"type": "reminder", "invoice_id": "i1"})
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import pop_job
            result = pop_job("emails", timeout=0)
        assert result == {"type": "reminder", "invoice_id": "i1"}

    def test_uses_blpop_when_timeout_positive(self):
        live_cache, mock_redis = _make_available_cache()
        mock_redis.blpop.return_value = ("job:emails", json.dumps({"type": "alert"}))
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import pop_job
            result = pop_job("emails", timeout=5)
        mock_redis.blpop.assert_called_once_with("job:emails", timeout=5)
        assert result == {"type": "alert"}


# ─────────────────────────────────────────────────────────────────────────────
# queue_length
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueLength:
    def test_returns_zero_when_unavailable(self):
        dead_cache = _unavailable_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", dead_cache):
            from smart_invoice_pro.services.job_queue import queue_length
            assert queue_length("emails") == 0

    def test_returns_llen(self):
        live_cache, mock_redis = _make_available_cache()
        mock_redis.llen.return_value = 7
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import queue_length
            assert queue_length("emails") == 7


# ─────────────────────────────────────────────────────────────────────────────
# move_to_dlq
# ─────────────────────────────────────────────────────────────────────────────

class TestMoveToDLQ:
    def test_no_op_when_unavailable(self):
        dead_cache = _unavailable_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", dead_cache):
            from smart_invoice_pro.services.job_queue import move_to_dlq
            move_to_dlq("emails", {"type": "failed"})  # must not raise

    def test_pushes_to_dlq_and_trims(self):
        live_cache, mock_redis = _make_available_cache()
        with patch("smart_invoice_pro.services.job_queue.cache", live_cache):
            from smart_invoice_pro.services.job_queue import move_to_dlq, _DLQ_MAX_LENGTH
            move_to_dlq("emails", {"type": "failed", "invoice_id": "x"})
        mock_redis.rpush.assert_called_once()
        assert mock_redis.rpush.call_args[0][0] == "job:emails:dlq"
        mock_redis.ltrim.assert_called_once_with(
            "job:emails:dlq", -_DLQ_MAX_LENGTH, -1
        )

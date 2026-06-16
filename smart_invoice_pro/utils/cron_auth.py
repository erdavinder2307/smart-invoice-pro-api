"""Shared secret guard for /api/cron/* job endpoints."""

import os
from functools import wraps

from flask import jsonify, request


def _cron_secret_valid() -> bool:
    expected = (os.getenv("CRON_SECRET") or "").strip()
    if not expected:
        return False
    provided = (
        request.headers.get("X-Cron-Secret", "").strip()
        or request.args.get("cron_secret", "").strip()
    )
    return provided == expected


def enforce_cron_secret():
    """Return a Flask response if cron secret is missing/invalid, else None."""
    if _cron_secret_valid():
        return None
    return jsonify({"error": "Forbidden — valid cron secret required"}), 403


def require_cron_secret(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        error = enforce_cron_secret()
        if error:
            return error
        return f(*args, **kwargs)

    return decorated

import os
from functools import wraps

import jwt
from flask import g, jsonify, request


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("SECRET_KEY", "your_secret_key"))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/demo-login",
    "/api/auth/demo-roles",
    "/api/ping",
    "/api/payments/webhook",
}


def _is_cron_path(path: str) -> bool:
    return path.startswith("/api/cron/")


def _unauthorized(message):
    return jsonify({"error": message}), 401


def _extract_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    return token or None


def _set_request_context(user_id, tenant_id, session_id=None, is_demo=False):
    g.user_id = user_id
    g.tenant_id = tenant_id
    g.is_demo = bool(is_demo)

    # Keep compatibility with the requested contract.
    setattr(request, "user_id", user_id)
    setattr(request, "tenant_id", tenant_id)
    setattr(request, "is_demo", bool(is_demo))
    # Used by me_api.get_sessions() to mark the current session
    if session_id:
        setattr(request, "token_id", session_id)


def authenticate_request_context():
    token = _extract_bearer_token()
    if not token:
        return None, _unauthorized("Unauthorized")

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None, _unauthorized("Unauthorized")
    except jwt.InvalidTokenError:
        return None, _unauthorized("Unauthorized")

    user_id = payload.get("user_id") or payload.get("id")
    tenant_id = payload.get("tenant_id")
    session_id = payload.get("session_id")

    # Backward compatibility for old tokens that had only id.
    if not tenant_id and payload.get("id"):
        tenant_id = payload.get("id")

    if not user_id or not tenant_id:
        return None, _unauthorized("Unauthorized")

    _set_request_context(
        user_id,
        tenant_id,
        session_id,
        is_demo=bool(payload.get("is_demo")),
    )
    return payload, None


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        payload, error_response = authenticate_request_context()
        if error_response:
            return error_response
        return f(*args, **kwargs)

    return decorated


def should_skip_auth(path, method):
    if method == "OPTIONS":
        return True

    if path in EXEMPT_PATHS:
        return True

    return not path.startswith("/api")


def enforce_api_auth():
    if should_skip_auth(request.path, request.method):
        return None

    if _is_cron_path(request.path):
        from smart_invoice_pro.utils.cron_auth import enforce_cron_secret
        return enforce_cron_secret()

    _, error_response = authenticate_request_context()
    if error_response:
        return error_response

    return None


def super_admin_required(f):
    """Decorator that enforces super-admin access.

    Must be applied *after* JWT auth (enforce_api_auth already runs as
    before_request, so the token is decoded).  Checks ``is_super_admin``
    in the JWT payload.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Re-decode the token to inspect the full payload
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "Unauthorized"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        except jwt.InvalidTokenError:
            return jsonify({"error": "Unauthorized"}), 401

        if payload.get("is_super_admin") is not True:
            return jsonify({"error": "Forbidden — super admin access required"}), 403

        return f(*args, **kwargs)
    return decorated

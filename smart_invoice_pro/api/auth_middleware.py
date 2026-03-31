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
}


def _unauthorized(message):
    return jsonify({"error": message}), 401


def _extract_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    return token or None


def _set_request_context(user_id, tenant_id):
    g.user_id = user_id
    g.tenant_id = tenant_id

    # Keep compatibility with the requested contract.
    setattr(request, "user_id", user_id)
    setattr(request, "tenant_id", tenant_id)


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

    # Backward compatibility for old tokens that had only id.
    if not tenant_id and payload.get("id"):
        tenant_id = payload.get("id")

    if not user_id or not tenant_id:
        return None, _unauthorized("Unauthorized")

    _set_request_context(user_id, tenant_id)
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

    _, error_response = authenticate_request_context()
    if error_response:
        return error_response

    return None

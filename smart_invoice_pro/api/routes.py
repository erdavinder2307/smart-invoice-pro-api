from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container, refresh_tokens_container
import uuid
import os
import secrets
from flasgger import swag_from
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import datetime
from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.api.auth_middleware import get_jwt_secret
from smart_invoice_pro.utils import email_verification, invites
import re

_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

try:
    import user_agents as _ua_lib
    _UA_AVAILABLE = True
except ImportError:
    _UA_AVAILABLE = False


def _parse_device_info(ua_string: str) -> dict:
    """Parse User-Agent string into structured device metadata."""
    if not ua_string:
        return {
            "browser": "", "browser_version": "",
            "os": "", "device_type": "Desktop", "device_name": "",
        }
    if not _UA_AVAILABLE:
        return {
            "browser": "", "browser_version": "",
            "os": "", "device_type": "Desktop", "device_name": "",
        }
    try:
        ua = _ua_lib.parse(ua_string)
        browser = ua.browser.family or ""
        browser_version = (ua.browser.version_string or "").split(".")[0]  # major only
        os_family = ua.os.family or ""
        if ua.is_mobile:
            device_type = "Mobile"
        elif ua.is_tablet:
            device_type = "Tablet"
        else:
            device_type = "Desktop"
        # Clean up "Other" placeholders
        if browser == "Other":
            browser = ""
        if os_family == "Other":
            os_family = ""
        device_name = " on ".join(filter(None, [browser, os_family])) or ""
        return {
            "browser": browser,
            "browser_version": browser_version,
            "os": os_family,
            "device_type": device_type,
            "device_name": device_name,
        }
    except Exception:
        return {
            "browser": "", "browser_version": "",
            "os": "", "device_type": "Desktop", "device_name": "",
        }


def _get_client_ip() -> str:
    """Extract real client IP, honouring X-Forwarded-For for reverse proxies."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""

api_blueprint = Blueprint('api_core', __name__)
auth_blueprint = Blueprint('auth', __name__)

_LOGIN_RATE_LIMIT = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 900

_DEMO_RATE_LIMIT = {}
_DEMO_MAX_ATTEMPTS = 30
_DEMO_WINDOW_SECONDS = 3600

PUBLIC_DEMO_ROLES = ("Sales", "Manager", "Accountant", "Purchaser")

# Internal role keys → public persona labels (marketing-facing)
_DEMO_ROLE_PUBLIC_META = {
    "Manager": {
        "title": "Business Owner",
        "description": "Dashboard, reports, banking, and full operational overview",
    },
    "Accountant": {
        "title": "Finance Manager",
        "description": "Banking, reconciliation, invoices, bills, and reports",
    },
    "Sales": {
        "title": "Sales Manager",
        "description": "Quotes, customers, sales orders, and invoices",
    },
    "Purchaser": {
        "title": "Accounts Executive",
        "description": "Invoices, payments, customers, and vendor payables",
    },
}


def _demo_enabled() -> bool:
    return os.getenv("DEMO_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _demo_tenant_id() -> str | None:
    value = (os.getenv("DEMO_TENANT_ID") or "").strip()
    return value or None


def _demo_rate_limit_exceeded() -> bool:
    ip = _get_client_ip() or "unknown"
    now = datetime.datetime.utcnow()
    bucket = _DEMO_RATE_LIMIT.get(ip)
    if not bucket or (now - bucket["window_start"]).total_seconds() > _DEMO_WINDOW_SECONDS:
        _DEMO_RATE_LIMIT[ip] = {"count": 0, "window_start": now}
        bucket = _DEMO_RATE_LIMIT[ip]
    if bucket["count"] >= _DEMO_MAX_ATTEMPTS:
        return True
    bucket["count"] += 1
    return False


def _issue_auth_tokens(user_doc: dict):
    """Create refresh token record + JWT access token for an authenticated user."""
    jwt_secret = get_jwt_secret()
    tenant_id = user_doc.get("tenant_id") or user_doc.get("id")
    user_id = user_doc["id"]
    is_demo = bool(user_doc.get("is_demo_user"))

    refresh_token_value = secrets.token_urlsafe(48)
    refresh_token_expires = datetime.datetime.utcnow() + datetime.timedelta(
        hours=4 if is_demo else 24 * 30
    )
    now_iso = datetime.datetime.utcnow().isoformat()
    token_record_id = str(uuid.uuid4())
    raw_ua = request.headers.get("User-Agent", "")
    device_info = _parse_device_info(raw_ua)
    client_ip = _get_client_ip()
    refresh_token_record = {
        "id": token_record_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "token": refresh_token_value,
        "expires_at": refresh_token_expires.isoformat(),
        "created_at": now_iso,
        "last_active_at": now_iso,
        "raw_user_agent": raw_ua,
        "browser": device_info["browser"],
        "browser_version": device_info["browser_version"],
        "os": device_info["os"],
        "device_type": device_info["device_type"],
        "device_name": device_info["device_name"],
        "ip_address": client_ip,
        "is_demo": is_demo,
    }
    refresh_tokens_container.create_item(body=refresh_token_record)

    token_payload = {
        "id": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "username": user_doc.get("username", ""),
        "role": user_doc.get("role", ""),
        "role_id": user_doc.get("role_id", ""),
        "is_super_admin": bool(user_doc.get("is_super_admin", False)),
        "is_demo": is_demo,
        "session_id": token_record_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
    }
    access_token = jwt.encode(token_payload, jwt_secret, algorithm="HS256")
    return access_token, refresh_token_value, tenant_id, user_id


def _login_rate_limit_exceeded():
    """Simple per-IP login attempt limiter (10 failures per 15 minutes)."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    now = datetime.datetime.utcnow()
    bucket = _LOGIN_RATE_LIMIT.get(ip)
    if not bucket or (now - bucket['window_start']).total_seconds() > _LOGIN_WINDOW_SECONDS:
        _LOGIN_RATE_LIMIT[ip] = {'count': 0, 'window_start': now}
        bucket = _LOGIN_RATE_LIMIT[ip]
    if bucket['count'] >= _LOGIN_MAX_ATTEMPTS:
        return True
    return False


def _record_failed_login():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    bucket = _LOGIN_RATE_LIMIT.get(ip)
    if bucket:
        bucket['count'] += 1

@api_blueprint.route('/ping', methods=['GET'])
def ping():
    return jsonify({"message": "pong"}), 200

def validate_json_request():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON."}), 400
    try:
        return request.get_json()
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400

@auth_blueprint.route('/auth/register', methods=['POST'])
@swag_from({
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'username': {
                        'type': 'string',
                        'description': 'The username of the user.'
                    },
                    'password': {
                        'type': 'string',
                        'description': 'The password of the user.'
                    }
                },
                'required': ['username', 'password']
            },
            'description': 'JSON object containing username and password.'
        }
    ],
    'responses': {
        '201': {
            'description': 'User registered successfully',
            'examples': {
                'application/json': {
                    'message': 'User registered successfully!',
                    'user': {
                        'id': 'uuid',
                        'username': 'example_user'
                    }
                }
            }
        }
    }
})
def register_user():
    data = validate_json_request()
    if isinstance(data, tuple):
        return data  # Return error response if JSON is invalid

    hashed_password = generate_password_hash(data['password'],method='pbkdf2:sha256', salt_length=16)
    # Self sign-up always creates a new tenant owned by this user, so tenant_id and
    # role in the body are ignored. Joining an existing tenant goes through an
    # Admin's invite (POST /api/settings/users), which takes the tenant from the token.
    tenant_id = str(uuid.uuid4())

    user_id = str(uuid.uuid4())
    username = data['username'].strip()
    email = username.lower() if '@' in username else (data.get('email') or '').strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "A valid email address is required."}), 400
    user = {
        'id': user_id,
        'userid': user_id,  # partition key field for Cosmos DB
        'tenant_id': tenant_id,
        'username': username,
        'email': email,
        'name': (data.get('name') or username).strip(),
        'password': hashed_password,
        'role': 'Admin',
        'is_active': True,
        'created_at': datetime.datetime.utcnow().isoformat()
    }
    verification_token = email_verification.issue_token(user)
    users_container.create_item(body=user)
    verification_sent = email_verification.send_verification_email(email, user['name'], verification_token)

    from smart_invoice_pro.utils.tenant_service import ensure_tenant_exists
    try:
        ensure_tenant_exists(
            tenant_id,
            name=(data.get('organization_name') or data.get('name') or username).strip(),
            owner_user_id=user_id,
        )
    except Exception:
        pass

    return jsonify({
        "message": "User registered successfully!",
        "user": {
            "id": user['id'],
            "tenant_id": user['tenant_id'],
            "username": user['username'],
            "role": user['role'],
            "email_verified": False,
        },
        "verification_email_sent": verification_sent,
    }), 201

@auth_blueprint.route('/auth/login', methods=['POST'])
@swag_from({
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'username': {
                        'type': 'string',
                        'description': 'The username of the user.'
                    },
                    'password': {
                        'type': 'string',
                        'description': 'The password of the user.'
                    }
                },
                'required': ['username', 'password']
            },
            'description': 'JSON object containing username and password.'
        }
    ],
    'responses': {
        '200': {
            'description': 'Login successful',
            'examples': {
                'application/json': {
                    'message': 'Login successful!',
                    'user': {
                        'id': 'uuid',
                        'username': 'example_user'
                    },
                    'token': 'jwt_token'
                }
            }
        },
        '401': {
            'description': 'Invalid username or password',
            'examples': {
                'application/json': {
                    'message': 'Invalid username or password.'
                }
            }
        }
    }
})
def login_user():
    data = validate_json_request()
    if isinstance(data, tuple):
        return data  # Return error response if JSON is invalid

    if _login_rate_limit_exceeded():
        return jsonify({
            "message": "Too many login attempts. Please try again in 15 minutes.",
        }), 429

    login_input = data['username'].strip()

    # Try matching by username first, then fall back to email
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.username = @username",
        parameters=[{"name": "@username", "value": login_input}],
        enable_cross_partition_query=True
    ))
    if not items:
        # Allow login with email address
        items = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.email = @email",
            parameters=[{"name": "@email", "value": login_input.lower()}],
            enable_cross_partition_query=True
        ))

    if items and check_password_hash(items[0]['password'], data['password']):
        jwt_secret = get_jwt_secret()
        tenant_id = items[0].get('tenant_id') or items[0].get('id')
        user_id = items[0]['id']

        # Generate and store refresh token (30-day expiry) with device metadata
        refresh_token_value = secrets.token_urlsafe(48)
        refresh_token_expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        now_iso = datetime.datetime.utcnow().isoformat()
        token_record_id = str(uuid.uuid4())
        raw_ua = request.headers.get("User-Agent", "")
        device_info = _parse_device_info(raw_ua)
        client_ip = _get_client_ip()
        refresh_token_record = {
            "id": token_record_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "token": refresh_token_value,
            "expires_at": refresh_token_expires.isoformat(),
            "created_at": now_iso,
            "last_active_at": now_iso,
            # Device metadata
            "raw_user_agent": raw_ua,
            "browser": device_info["browser"],
            "browser_version": device_info["browser_version"],
            "os": device_info["os"],
            "device_type": device_info["device_type"],
            "device_name": device_info["device_name"],
            "ip_address": client_ip,
        }
        refresh_tokens_container.create_item(body=refresh_token_record)

        access_token = jwt.encode(
            {
                "id": user_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "role": items[0].get('role', ''),
                "role_id": items[0].get('role_id', ''),
                "is_super_admin": bool(items[0].get('is_super_admin', False)),
                "session_id": token_record_id,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
            },
            jwt_secret,
            algorithm="HS256"
        )

        log_audit_event({
            "action": "LOGIN",
            "entity": "auth",
            "entity_id": user_id,
            "before": None,
            "after": {
                "login": "success",
                "username": items[0]['username'],
                "is_super_admin": bool(items[0].get('is_super_admin', False)),
            },
            "metadata": {
                "event": "login",
            },
            "tenant_id": tenant_id,
            "user_id": user_id,
            "user_email": items[0].get("email") or items[0].get("username"),
        })

        # Look up user profile to get display name
        profile_name = ''
        try:
            profile_query = f"SELECT c.name FROM c WHERE c.type = 'user_profile' AND c.user_id = '{user_id}'"
            profiles = list(users_container.query_items(query=profile_query, enable_cross_partition_query=True))
            if profiles and profiles[0].get('name'):
                profile_name = profiles[0]['name']
        except Exception:
            pass

        return jsonify({
            "message": "Login successful!",
            "user": {
                "id": user_id,
                "tenant_id": tenant_id,
                "username": items[0]['username'],
                "name": profile_name or items[0].get('name', ''),
                "role": items[0].get('role', 'Sales'),
                "is_super_admin": bool(items[0].get('is_super_admin', False)),
                "email_verified": email_verification.is_verified(items[0]),
            },
            "token": access_token,
            "access_token": access_token,
            "refresh_token": refresh_token_value
        }), 200
    else:
        _record_failed_login()
        attempted_user = items[0] if items else None
        log_audit_event({
            "action": "LOGIN_FAILED",
            "entity": "auth",
            "entity_id": attempted_user.get("id") if attempted_user else None,
            "before": None,
            "after": {
                "login": "failed",
                "username_attempt": login_input,
            },
            "metadata": {"event": "login_failed"},
            "tenant_id": (
                attempted_user.get("tenant_id") or attempted_user.get("id")
                if attempted_user else "unknown"
            ),
            "user_id": attempted_user.get("id") if attempted_user else None,
            "user_email": (
                (attempted_user.get("email") or attempted_user.get("username"))
                if attempted_user else login_input
            ),
            "ip_address": _get_client_ip(),
        })
        return jsonify({"message": "Invalid username or password."}), 401


@auth_blueprint.route('/auth/demo-roles', methods=['GET'])
def demo_roles():
    """Public list of demo personas (no credentials)."""
    if not _demo_enabled():
        return jsonify({"error": "Demo is not enabled."}), 404

    roles = []
    for role in PUBLIC_DEMO_ROLES:
        meta = _DEMO_ROLE_PUBLIC_META.get(role, {})
        roles.append({
            "role": role,
            "title": meta.get("title", role),
            "description": meta.get("description", ""),
        })
    return jsonify({"roles": roles}), 200


@auth_blueprint.route('/auth/demo-login', methods=['POST'])
def demo_login():
    """
    Passwordless demo entry for a fixed demo tenant persona.
    Requires DEMO_ENABLED=true and DEMO_TENANT_ID on the server.
    """
    if not _demo_enabled():
        return jsonify({"error": "Demo is not enabled."}), 404

    demo_tenant_id = _demo_tenant_id()
    if not demo_tenant_id:
        return jsonify({"error": "Demo tenant is not configured."}), 503

    if _demo_rate_limit_exceeded():
        return jsonify({
            "message": "Too many demo sessions. Please try again later.",
        }), 429

    data = validate_json_request()
    if isinstance(data, tuple):
        return data

    role = (data.get("role") or "").strip()
    if role not in PUBLIC_DEMO_ROLES:
        return jsonify({
            "error": "Invalid demo role.",
            "allowed_roles": list(PUBLIC_DEMO_ROLES),
        }), 400

    username = f"demo-{role.lower()}"
    items = list(users_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.tenant_id = @tid "
            "AND c.username = @username AND c.is_demo_user = true"
        ),
        parameters=[
            {"name": "@tid", "value": demo_tenant_id},
            {"name": "@username", "value": username},
        ],
        enable_cross_partition_query=True,
    ))

    if not items or not items[0].get("is_active", True):
        return jsonify({
            "error": "Demo persona is not available. Try again later.",
        }), 503

    user_doc = items[0]
    access_token, refresh_token_value, tenant_id, user_id = _issue_auth_tokens(user_doc)

    log_audit_event({
        "action": "DEMO_LOGIN",
        "entity": "auth",
        "entity_id": user_id,
        "before": None,
        "after": {"login": "success", "role": role, "demo": True},
        "metadata": {"event": "demo_login", "role": role},
        "tenant_id": tenant_id,
        "user_id": user_id,
        "user_email": user_doc.get("email") or username,
        "ip_address": _get_client_ip(),
    })

    return jsonify({
        "message": "Demo session started.",
        "user": {
            "id": user_id,
            "tenant_id": tenant_id,
            "username": user_doc.get("username", username),
            "name": user_doc.get("name", f"Demo {role}"),
            "role": user_doc.get("role", role),
            "is_super_admin": False,
            "is_demo": True,
        },
        "token": access_token,
        "access_token": access_token,
        "refresh_token": refresh_token_value,
    }), 200


@auth_blueprint.route('/auth/refresh', methods=['POST'])
def refresh_token():
    """
    Exchange a valid refresh token for a new access token.
    Input JSON: { "refresh_token": "<token>" }
    Returns: { "access_token": "<new_jwt>" }
    """
    data = request.get_json(silent=True) or {}
    incoming = data.get('refresh_token', '').strip()
    if not incoming:
        return jsonify({"error": "refresh_token is required"}), 400

    # Look up token in DB
    items = list(refresh_tokens_container.query_items(
        query="SELECT * FROM c WHERE c.token = @token",
        parameters=[{"name": "@token", "value": incoming}],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({"error": "Invalid refresh token"}), 401

    record = items[0]

    # Check expiry
    expires_at = datetime.datetime.fromisoformat(record['expires_at'])
    if datetime.datetime.utcnow() > expires_at:
        # Expired — delete stale record
        try:
            refresh_tokens_container.delete_item(
                item=record['id'], partition_key=record['user_id']
            )
        except Exception:
            pass
        return jsonify({"error": "Refresh token expired"}), 401

    # Issue new access token
    jwt_secret = get_jwt_secret()

    # Fetch user to get current role/username
    user_items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": record['user_id']}],
        enable_cross_partition_query=True
    ))
    if not user_items:
        return jsonify({"error": "User not found"}), 401

    user = user_items[0]
    is_demo = bool(record.get("is_demo") or user.get("is_demo_user"))
    # Rotate refresh token — preserve device metadata, update last_active_at
    new_refresh_value = secrets.token_urlsafe(48)
    refresh_hours = 4 if is_demo else 24 * 30
    new_expires = datetime.datetime.utcnow() + datetime.timedelta(hours=refresh_hours)
    new_token_record_id = str(uuid.uuid4())
    now_iso = datetime.datetime.utcnow().isoformat()
    # Delete old record
    try:
        refresh_tokens_container.delete_item(
            item=record['id'], partition_key=record['user_id']
        )
    except Exception:
        pass
    # Create rotated record, carrying over device metadata
    refresh_tokens_container.create_item(body={
        "id": new_token_record_id,
        "user_id": record['user_id'],
        "tenant_id": record['tenant_id'],
        "token": new_refresh_value,
        "expires_at": new_expires.isoformat(),
        "created_at": record.get('created_at', now_iso),
        "last_active_at": now_iso,
        # Carry over device metadata from original login
        "raw_user_agent": record.get('raw_user_agent', ''),
        "browser": record.get('browser', ''),
        "browser_version": record.get('browser_version', ''),
        "os": record.get('os', ''),
        "device_type": record.get('device_type', 'Desktop'),
        "device_name": record.get('device_name', ''),
        "ip_address": record.get('ip_address', ''),
        "is_demo": is_demo,
    })

    new_access_token = jwt.encode(
        {
            "id": record['user_id'],
            "user_id": record['user_id'],
            "tenant_id": record['tenant_id'],
            "username": user.get('username', ''),
            "role": user.get('role', ''),
            "role_id": user.get('role_id', ''),
            "is_super_admin": bool(user.get('is_super_admin', False)),
            "is_demo": is_demo,
            "session_id": new_token_record_id,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
        },
        jwt_secret,
        algorithm="HS256"
    )

    return jsonify({
        "access_token": new_access_token,
        "token": new_access_token,
        "refresh_token": new_refresh_value
    }), 200


@auth_blueprint.route('/auth/logout', methods=['POST'])
def logout_user():
    """
    Logout endpoint. Revokes the provided refresh token from the DB.
    Input JSON (optional): { "refresh_token": "<token>" }
    """
    data = request.get_json(silent=True) or {}
    incoming = data.get('refresh_token', '').strip()
    actor_user_id = None
    actor_tenant_id = None
    if incoming:
        items = list(refresh_tokens_container.query_items(
            query="SELECT * FROM c WHERE c.token = @token",
            parameters=[{"name": "@token", "value": incoming}],
            enable_cross_partition_query=True
        ))
        for record in items:
            actor_user_id = record.get("user_id")
            actor_tenant_id = record.get("tenant_id")
            try:
                refresh_tokens_container.delete_item(
                    item=record['id'], partition_key=record['user_id']
                )
            except Exception:
                pass

    if actor_tenant_id:
        log_audit_event({
            "action": "LOGOUT",
            "entity": "auth",
            "entity_id": actor_user_id,
            "before": None,
            "after": {"logout": "success"},
            "metadata": {"event": "logout"},
            "tenant_id": actor_tenant_id,
            "user_id": actor_user_id,
        })

    return jsonify({"message": "Logout successful."}), 200


@auth_blueprint.route('/auth/verify-email', methods=['POST'])
def verify_email():
    """
    Confirm an email address with the one-time token from the sign-up email.
    Public (the link may be opened signed out). Input JSON: { "token": "<token>" }
    """
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    user = email_verification.find_user_by_token(token)
    if not user:
        return jsonify({"error": "This link is not valid or has already been used.",
                        "code": "TOKEN_INVALID"}), 400
    if email_verification.token_expired(user):
        return jsonify({"error": "This link has expired. Sign in and ask for a new one.",
                        "code": "TOKEN_EXPIRED"}), 400

    email_verification.mark_verified(user)
    users_container.replace_item(item=user['id'], body=user)
    log_audit_event({
        "action": "EMAIL_VERIFIED",
        "entity": "auth",
        "entity_id": user['id'],
        "before": None,
        "after": {"email_verified": True},
        "metadata": {"event": "email_verified"},
        "tenant_id": user.get('tenant_id'),
        "user_id": user['id'],
    })
    return jsonify({"message": "Email verified.", "email_verified": True}), 200


@auth_blueprint.route('/auth/resend-verification', methods=['POST'])
def resend_verification():
    """Send a new verification link to the signed-in user (old links stop working)."""
    user_id = getattr(request, 'user_id', None)
    user = email_verification.load_user(user_id) if user_id else None
    if not isinstance(user, dict):
        return jsonify({"error": "Unauthorized"}), 401
    if email_verification.is_verified(user):
        return jsonify({"message": "Email already verified.", "email_verified": True}), 200
    if email_verification.resend_too_soon(user):
        return jsonify({"error": "Please wait a minute before asking for another email."}), 429

    token = email_verification.issue_token(user)
    users_container.replace_item(item=user['id'], body=user)
    sent = email_verification.send_verification_email(user.get('email'), user.get('name'), token)
    if not sent:
        return jsonify({"error": "We could not send the email right now. Please try again later."}), 503
    return jsonify({"message": "Verification email sent.", "email_verified": False}), 200


@auth_blueprint.route('/auth/accept-invite', methods=['POST'])
def accept_invite():
    """
    Set your own password from an Admin's one-time invite link.
    Public. Input JSON: { "token": "<token>", "password": "<new password>" }
    """
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    password = data.get('password') or ''
    if len(password) < invites.MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Password must be at least {invites.MIN_PASSWORD_LENGTH} characters."}), 400

    user = invites.find_user_by_invite_token(token)
    if not user or not user.get('invite_pending'):
        return jsonify({"error": "This invite link is not valid or has already been used.",
                        "code": "TOKEN_INVALID"}), 400
    if invites.invite_expired(user):
        return jsonify({"error": "This invite link has expired. Ask your admin to send a new one.",
                        "code": "TOKEN_EXPIRED"}), 400
    if user.get('is_active') is False:
        return jsonify({"error": "This account has been deactivated.", "code": "TOKEN_INVALID"}), 400

    invites.accept_invite(user, password)
    users_container.replace_item(item=user['id'], body=user)
    log_audit_event({
        "action": "INVITE_ACCEPTED",
        "entity": "auth",
        "entity_id": user['id'],
        "before": None,
        "after": {"invite_pending": False},
        "metadata": {"event": "invite_accepted"},
        "tenant_id": user.get('tenant_id'),
        "user_id": user['id'],
    })
    return jsonify({"message": "Password set. You can sign in now.",
                    "username": user.get('username', '')}), 200


@auth_blueprint.route('/auth/delete-account', methods=['DELETE'])
def delete_account():
    """
    Permanently delete the authenticated user's account and all associated data.
    Requires a valid Bearer token (validated by enforce_api_auth before_request).
    """
    from smart_invoice_pro.utils.cosmos_client import (
        invoices_container, customers_container, products_container,
        stock_container, bank_accounts_container, quotes_container,
        recurring_profiles_container, sales_orders_container,
        vendors_container, purchase_orders_container, bills_container,
        expenses_container, settings_container
    )

    user_id = request.user_id
    tenant_id = request.tenant_id

    # Demo personas must not delete the shared demo tenant.
    try:
        token = request.headers.get("Authorization", "").split(" ", 1)[-1]
        payload = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=["HS256"],
        )
        if payload.get("is_demo"):
            return jsonify({"error": "Demo accounts cannot delete data."}), 403
    except Exception:
        pass

    user_items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    if user_items and user_items[0].get("is_demo_user"):
        return jsonify({"error": "Demo accounts cannot delete data."}), 403

    def _bulk_delete(container, partition_key_field):
        """Query all items for this tenant and delete each one."""
        try:
            items = list(container.query_items(
                query="SELECT * FROM c WHERE c.tenant_id = @tid",
                parameters=[{"name": "@tid", "value": tenant_id}],
                enable_cross_partition_query=True
            ))
            for item in items:
                pk_val = item.get(partition_key_field) or item.get('id')
                try:
                    container.delete_item(item=item['id'], partition_key=pk_val)
                except Exception:
                    pass
        except Exception:
            pass

    _bulk_delete(invoices_container, 'customer_id')
    _bulk_delete(customers_container, 'customer_id')
    _bulk_delete(products_container, 'product_id')
    _bulk_delete(stock_container, 'product_id')
    _bulk_delete(bank_accounts_container, 'user_id')
    _bulk_delete(quotes_container, 'customer_id')
    _bulk_delete(recurring_profiles_container, 'customer_id')
    _bulk_delete(sales_orders_container, 'customer_id')
    _bulk_delete(vendors_container, 'vendor_id')
    _bulk_delete(purchase_orders_container, 'vendor_id')
    _bulk_delete(bills_container, 'vendor_id')
    _bulk_delete(expenses_container, 'id')
    _bulk_delete(settings_container, 'tenant_id')

    # Delete the user record (partition key field: userid)
    try:
        items = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": user_id}],
            enable_cross_partition_query=True
        ))
        if items:
            u = items[0]
            users_container.delete_item(
                item=u['id'],
                partition_key=u.get('userid', u['id'])
            )
    except Exception:
        pass

    return jsonify({"message": "Account deleted successfully."}), 200

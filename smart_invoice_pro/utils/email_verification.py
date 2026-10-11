"""Email verification for self sign-up.

A new sign-up gets ``email_verified: False`` and a one-time link. Only a SHA-256
hash of the link's token is stored on the user, with an expiry. Users created
before this feature have no ``email_verified`` field and count as verified, so
nobody already using the product is locked out.

Unverified users can sign in and look around, but cannot create invoices until
they open the link (``require_verified_email``).
"""
import datetime
import hashlib
import logging
import os
import secrets
from html import escape

from flask import jsonify

from smart_invoice_pro.utils.cosmos_client import users_container

TOKEN_TTL = datetime.timedelta(hours=48)
RESEND_COOLDOWN = datetime.timedelta(seconds=60)

NOT_VERIFIED_CODE = "EMAIL_NOT_VERIFIED"
NOT_VERIFIED_MESSAGE = (
    "Please verify your email address before creating invoices. "
    "Open the link we sent you, or ask for a new one."
)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_verified(user):
    """Only an explicit False means unverified (older accounts have no field)."""
    return user.get("email_verified") is not False


def issue_token(user, now=None):
    """Put a fresh token hash and expiry on ``user`` (not saved). Returns the raw token."""
    now = now or datetime.datetime.utcnow()
    token = secrets.token_urlsafe(32)
    user["email_verified"] = False
    user["email_verification_hash"] = hash_token(token)
    user["email_verification_expires_at"] = (now + TOKEN_TTL).isoformat()
    user["email_verification_sent_at"] = now.isoformat()
    return token


def resend_too_soon(user, now=None):
    sent_at = user.get("email_verification_sent_at")
    if not sent_at:
        return False
    now = now or datetime.datetime.utcnow()
    try:
        return now - datetime.datetime.fromisoformat(sent_at) < RESEND_COOLDOWN
    except ValueError:
        return False


def find_user_by_token(token):
    if not token:
        return None
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.email_verification_hash = @h",
        parameters=[{"name": "@h", "value": hash_token(token)}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def token_expired(user, now=None):
    now = now or datetime.datetime.utcnow()
    try:
        return now > datetime.datetime.fromisoformat(user.get("email_verification_expires_at") or "")
    except ValueError:
        return True


def mark_verified(user, now=None):
    now = now or datetime.datetime.utcnow()
    user["email_verified"] = True
    user["email_verified_at"] = now.isoformat()
    for key in ("email_verification_hash", "email_verification_expires_at", "email_verification_sent_at"):
        user.pop(key, None)
    return user


def load_user(user_id):
    try:
        return users_container.read_item(item=user_id, partition_key=user_id)
    except Exception:
        return None


def require_verified_email(user_id):
    """Return a 403 response when the signed-in user has not verified their email, else None."""
    user = load_user(user_id) if user_id else None
    if isinstance(user, dict) and not is_verified(user):
        return jsonify({"error": NOT_VERIFIED_MESSAGE, "code": NOT_VERIFIED_CODE}), 403
    return None


def send_verification_email(to_email, to_name, token):
    """Send the link through Azure Communication Services. Failures are logged, never raised."""
    from smart_invoice_pro.utils.email_client import email_configured, get_email_client

    if not to_email or "@" not in to_email:
        logging.warning("email verification: user has no email address; link not sent")
        return False
    if not email_configured():
        logging.warning("email verification: email service not configured; link not sent")
        return False

    app_url = os.getenv("APP_URL", "https://app.solidevbooks.com").rstrip("/")
    sender_address = os.getenv("SENDER_EMAIL", "noreply@solidevelectrosoft.com")
    link = f"{app_url}/verify-email?token={token}"
    name = escape(to_name or "there")
    hours = int(TOKEN_TTL.total_seconds() // 3600)

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:32px 16px">
  <table width="560" align="center" style="background:#fff;border-radius:8px;border:1px solid #e2e8f0;padding:36px">
    <tr><td>
      <h2 style="margin:0 0 8px;color:#1a202c;font-size:20px">Confirm your email</h2>
      <p style="color:#4a5568;font-size:14px;line-height:1.6;margin:0 0 24px">
        Hi {name},<br><br>
        Please confirm this email address to start creating invoices in <strong>Solidev Books</strong>.
      </p>
      <p style="margin:0 0 24px">
        <a href="{escape(link)}" style="display:inline-block;background:#1a56db;color:#fff;text-decoration:none;
           padding:12px 28px;border-radius:6px;font-size:14px;font-weight:600">Confirm email</a>
      </p>
      <p style="font-size:12px;color:#a0aec0;margin:0">
        The link works once and expires in {hours} hours. If you did not sign up, you can ignore this email.
      </p>
    </td></tr>
  </table>
</body></html>
"""
    plain_body = (
        f"Hi {to_name or 'there'},\n\n"
        "Please confirm this email address to start creating invoices in Solidev Books:\n"
        f"{link}\n\n"
        f"The link works once and expires in {hours} hours. "
        "If you did not sign up, you can ignore this email."
    )
    try:
        client = get_email_client()
        poller = client.begin_send({
            "senderAddress": sender_address,
            "recipients": {"to": [{"address": to_email, "displayName": to_name or to_email}]},
            "content": {
                "subject": "Confirm your email — Solidev Books",
                "html": html_body,
                "plainText": plain_body,
            },
        })
        poller.result(timeout=30)
        return True
    except Exception as exc:
        logging.error("email verification: send failed: %s", type(exc).__name__)
        return False

"""One-time invite links: an Admin invites a person, the person sets their own password.

The user is created with an unusable random password and ``invite_pending: True``.
Only a SHA-256 hash of the link's token is stored, with an expiry. No password is
ever chosen by the Admin or sent by email.
"""
import datetime
import secrets

from werkzeug.security import generate_password_hash

from smart_invoice_pro.utils.cosmos_client import users_container
from smart_invoice_pro.utils.email_verification import hash_token

INVITE_TTL = datetime.timedelta(days=7)
MIN_PASSWORD_LENGTH = 8


def unusable_password_hash():
    return generate_password_hash(secrets.token_urlsafe(32), method='pbkdf2:sha256', salt_length=16)


def issue_invite(user, now=None):
    """Put a fresh invite token hash and expiry on ``user`` (not saved). Returns the raw token."""
    now = now or datetime.datetime.utcnow()
    token = secrets.token_urlsafe(32)
    user['invite_pending'] = True
    user['invite_token_hash'] = hash_token(token)
    user['invite_expires_at'] = (now + INVITE_TTL).isoformat()
    return token


def find_user_by_invite_token(token):
    if not token:
        return None
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.invite_token_hash = @h",
        parameters=[{"name": "@h", "value": hash_token(token)}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def invite_expired(user, now=None):
    now = now or datetime.datetime.utcnow()
    try:
        return now > datetime.datetime.fromisoformat(user.get('invite_expires_at') or '')
    except ValueError:
        return True


def accept_invite(user, password, now=None):
    """Set the invitee's own password and close the invite (not saved)."""
    now = now or datetime.datetime.utcnow()
    user['password'] = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
    user['invite_pending'] = False
    user['invite_accepted_at'] = now.isoformat()
    # Opening the emailed link proves the address belongs to them.
    user['email_verified'] = True
    user['updated_at'] = now.isoformat()
    for key in ('invite_token_hash', 'invite_expires_at'):
        user.pop(key, None)
    return user

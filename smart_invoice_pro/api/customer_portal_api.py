"""
Customer Portal API — invitation and account setup endpoints.

Phase B: invite/accept-invite (unified identity).
Phase C will add portal data endpoints (/api/portal/invoices, etc.).
"""
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

from smart_invoice_pro.utils.cosmos_client import customers_container, users_container
from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.permission_checker import require_permission

customer_portal_blueprint = Blueprint('customer_portal', __name__)

_INVITE_EXPIRY_HOURS = 72


# ── helpers ──────────────────────────────────────────────────────────────────

def _send_portal_invite_email(
    to_email: str,
    to_name: str,
    invite_token: str,
    tenant_id: str,
    org_name: str = 'Solidev Books',
) -> None:
    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')
    app_url = os.getenv('APP_URL', 'https://app.solidevbooks.com')

    if not connection_string:
        logging.warning("portal invite: AZURE_EMAIL_CONNECTION_STRING not set — skipping email")
        return

    accept_url = f"{app_url}/portal/accept-invite?token={invite_token}"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:0">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:32px 16px">
      <table width="560" style="background:#fff;border-radius:8px;
             border:1px solid #e2e8f0;padding:40px 36px">
        <tr><td>
          <h2 style="margin:0 0 8px;color:#1a202c;font-size:20px">
            You've been invited to the {org_name} Customer Portal
          </h2>
          <p style="color:#4a5568;font-size:14px;line-height:1.6;margin:0 0 24px">
            Hi {to_name},<br><br>
            You've been given access to the <strong>{org_name}</strong> customer portal,
            where you can view your invoices, quotes, and account history.<br><br>
            Click the button below to set up your password and access your account.
            This link expires in {_INVITE_EXPIRY_HOURS} hours.
          </p>

          <p style="margin:0 0 24px">
            <a href="{accept_url}"
               style="display:inline-block;background:#1a56db;color:#fff;
                      text-decoration:none;padding:12px 28px;border-radius:6px;
                      font-size:14px;font-weight:600">
              Set up your password
            </a>
          </p>

          <p style="font-size:12px;color:#a0aec0;margin:0">
            If you were not expecting this invitation, you can safely ignore this email.
            This link will expire in {_INVITE_EXPIRY_HOURS} hours.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    plain_body = (
        f"You've been invited to the {org_name} Customer Portal.\n\n"
        f"Hi {to_name},\n\n"
        f"Set up your password here: {accept_url}\n\n"
        f"This link expires in {_INVITE_EXPIRY_HOURS} hours."
    )

    try:
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send({
            "senderAddress": sender_address,
            "recipients": {"to": [{"address": to_email, "displayName": to_name}]},
            "content": {
                "subject": f"You've been invited to {org_name} — Customer Portal",
                "html": html_body,
                "plainText": plain_body,
            },
        })
        poller.result(timeout=30)
        logging.info(f"portal invite email sent to {to_email}")
    except Exception as exc:
        logging.error(f"portal invite: failed to send email to {to_email}: {exc}")


def _get_org_name(tenant_id: str) -> str:
    try:
        from smart_invoice_pro.api.organization_profile_api import _get_profile
        profile = _get_profile(tenant_id)
        return (profile.get('organization_name') or '').strip() or 'Solidev Books'
    except Exception:
        return 'Solidev Books'


# ── CPB-005: POST /api/portal/invite ─────────────────────────────────────────

@customer_portal_blueprint.route('/portal/invite', methods=['POST'])
@require_permission('user_management', 'invite')
def invite_customer():
    """
    Admin invites a customer to the portal.
    Body: { customer_id, email, name }
    Creates a user record in users_container with user_type='customer' and
    a time-limited invite_token, then sends the invite email.
    """
    try:
        data = request.get_json(silent=True) or {}
        customer_id = (data.get('customer_id') or '').strip()
        email = (data.get('email') or '').strip().lower()
        name = (data.get('name') or '').strip()

        if not customer_id or not email or '@' not in email:
            return jsonify({'error': 'customer_id and valid email are required'}), 400

        # Verify the customer record exists and belongs to this tenant
        customer_items = list(customers_container.query_items(
            query="SELECT * FROM c WHERE c.id = @cid AND c.tenant_id = @tid",
            parameters=[
                {"name": "@cid", "value": customer_id},
                {"name": "@tid", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        if not customer_items:
            return jsonify({'error': 'Customer not found'}), 404

        customer = customer_items[0]
        name = name or customer.get('name', email.split('@')[0])

        # Check if a portal user already exists for this customer
        existing = list(users_container.query_items(
            query=(
                "SELECT c.id FROM c WHERE c.tenant_id = @tid "
                "AND c.user_type = 'customer' AND c.linked_customer_id = @cid"
            ),
            parameters=[
                {"name": "@tid", "value": request.tenant_id},
                {"name": "@cid", "value": customer_id},
            ],
            enable_cross_partition_query=True,
        ))
        if existing:
            return jsonify({'error': 'A portal account already exists for this customer'}), 409

        # Check email uniqueness within tenant
        email_dupes = list(users_container.query_items(
            query="SELECT c.id FROM c WHERE c.email = @email AND c.tenant_id = @tid",
            parameters=[
                {"name": "@email", "value": email},
                {"name": "@tid", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        if email_dupes:
            return jsonify({'error': 'A user with this email already exists'}), 409

        invite_token = secrets.token_urlsafe(48)
        invite_expires = (datetime.utcnow() + timedelta(hours=_INVITE_EXPIRY_HOURS)).isoformat()
        user_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        user_doc = {
            'id': user_id,
            'userid': user_id,
            'tenant_id': request.tenant_id,
            'name': name,
            'username': email,
            'email': email,
            'password': '',               # set on accept-invite
            'role': 'Customer',
            'role_id': None,
            'user_type': 'customer',
            'linked_customer_id': customer_id,
            'is_active': False,           # activated on accept-invite
            'invited_by': request.user_id,
            'invite_token': invite_token,
            'invite_expires': invite_expires,
            'invite_accepted': False,
            'created_at': now,
            'updated_at': now,
        }
        users_container.create_item(body=user_doc)

        log_audit_event({
            "action": "CUSTOMER_PORTAL_INVITE",
            "entity": "user",
            "entity_id": user_id,
            "before": None,
            "after": {"email": email, "customer_id": customer_id},
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "user_email": email,
        })

        org_name = _get_org_name(request.tenant_id)
        _send_portal_invite_email(email, name, invite_token, request.tenant_id, org_name)

        return jsonify({
            "message": "Invitation sent",
            "user_id": user_id,
            "email": email,
        }), 201

    except Exception as exc:
        logging.error(f"invite_customer: {exc}")
        return jsonify({'error': str(exc)}), 500


# ── CPB-006: POST /api/portal/accept-invite ───────────────────────────────────

@customer_portal_blueprint.route('/portal/accept-invite', methods=['POST'])
def accept_portal_invite():
    """
    Public endpoint — customer accepts their invitation and sets a password.
    Body: { token, password }
    Added to EXEMPT_PATHS in auth_middleware.py.
    """
    try:
        data = request.get_json(silent=True) or {}
        token = (data.get('token') or '').strip()
        password = (data.get('password') or '').strip()

        if not token or not password:
            return jsonify({'error': 'token and password are required'}), 400
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Find user by invite token
        matches = list(users_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.invite_token = @token "
                "AND c.user_type = 'customer' AND c.invite_accepted = false"
            ),
            parameters=[{"name": "@token", "value": token}],
            enable_cross_partition_query=True,
        ))
        if not matches:
            return jsonify({'error': 'Invalid or already-used invitation link'}), 400

        user_doc = matches[0]

        # Check expiry
        expires_at = user_doc.get('invite_expires', '')
        if expires_at and datetime.utcnow() > datetime.fromisoformat(expires_at):
            return jsonify({'error': 'Invitation link has expired. Please contact your administrator.'}), 400

        # Activate the account
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
        user_doc['password'] = hashed_pw
        user_doc['is_active'] = True
        user_doc['invite_accepted'] = True
        user_doc['invite_token'] = None         # consume the token
        user_doc['activated_at'] = datetime.utcnow().isoformat()
        user_doc['updated_at'] = datetime.utcnow().isoformat()

        users_container.replace_item(item=user_doc['id'], body=user_doc)

        log_audit_event({
            "action": "CUSTOMER_PORTAL_ACTIVATED",
            "entity": "user",
            "entity_id": user_doc['id'],
            "before": None,
            "after": {"email": user_doc['email'], "is_active": True},
            "tenant_id": user_doc.get('tenant_id'),
            "user_id": user_doc['id'],
            "user_email": user_doc['email'],
        })

        return jsonify({
            "message": "Account activated. You can now log in at /login.",
        }), 200

    except Exception as exc:
        logging.error(f"accept_portal_invite: {exc}")
        return jsonify({'error': str(exc)}), 500

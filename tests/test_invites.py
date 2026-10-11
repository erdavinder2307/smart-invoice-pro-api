"""Invite links: the invitee sets their own password; resend replaces the link."""
import datetime
from unittest.mock import patch

from werkzeug.security import check_password_hash

from smart_invoice_pro.utils import invites
from smart_invoice_pro.utils.email_verification import hash_token
from tests.conftest import TENANT_A, TENANT_B, USER_A, auth_headers

INVITEE_ID = "user-invitee-1"
ADMIN_USER = {"id": USER_A, "userid": USER_A, "tenant_id": TENANT_A, "username": "admin",
              "email": "admin@example.com", "role": "Admin", "is_active": True, "password": "x"}


def _invitee(**extra):
    user = {"id": INVITEE_ID, "userid": INVITEE_ID, "tenant_id": TENANT_A, "username": "newbie",
            "email": "newbie@example.com", "name": "New", "role": "Sales", "is_active": True,
            "password": invites.unusable_password_hash()}
    token = invites.issue_invite(user)
    user.update(extra)
    return user, token


class TestAcceptInvite:

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.invites.users_container")
    def test_invitee_sets_own_password_once(self, mock_inv_users, mock_route_users, client):
        user, token = _invitee()
        mock_inv_users.query_items.return_value = [user]

        resp = client.post("/api/auth/accept-invite", json={"token": token, "password": "MyOwnPass1"})
        assert resp.status_code == 200
        saved = mock_route_users.replace_item.call_args.kwargs["body"]
        assert check_password_hash(saved["password"], "MyOwnPass1")
        assert saved["invite_pending"] is False
        assert saved["email_verified"] is True
        assert "invite_token_hash" not in saved
        assert mock_inv_users.query_items.call_args.kwargs["parameters"][0]["value"] == hash_token(token)

    @patch("smart_invoice_pro.utils.invites.users_container")
    def test_used_or_unknown_link_is_refused(self, mock_inv_users, client):
        mock_inv_users.query_items.return_value = []
        resp = client.post("/api/auth/accept-invite", json={"token": "nope", "password": "MyOwnPass1"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "TOKEN_INVALID"

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.invites.users_container")
    def test_expired_link_is_refused(self, mock_inv_users, mock_route_users, client):
        user, token = _invitee(
            invite_expires_at=(datetime.datetime.utcnow() - datetime.timedelta(minutes=1)).isoformat())
        mock_inv_users.query_items.return_value = [user]
        resp = client.post("/api/auth/accept-invite", json={"token": token, "password": "MyOwnPass1"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "TOKEN_EXPIRED"
        mock_route_users.replace_item.assert_not_called()

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.invites.users_container")
    def test_deactivated_invitee_cannot_accept(self, mock_inv_users, mock_route_users, client):
        user, token = _invitee(is_active=False)
        mock_inv_users.query_items.return_value = [user]
        resp = client.post("/api/auth/accept-invite", json={"token": token, "password": "MyOwnPass1"})
        assert resp.status_code == 400
        mock_route_users.replace_item.assert_not_called()

    def test_short_password_is_refused(self, client):
        resp = client.post("/api/auth/accept-invite", json={"token": "t", "password": "short"})
        assert resp.status_code == 400

    def test_new_invitee_cannot_sign_in_before_accepting(self):
        user, _ = _invitee()
        # The stored hash is random; no password an Admin or attacker could know matches it.
        assert not check_password_hash(user["password"], "")
        assert not check_password_hash(user["password"], "password123")


class TestResendInvite:

    def _post(self, client, target):
        with patch("smart_invoice_pro.api.roles_permissions_api.users_container") as mock_users, \
                patch("smart_invoice_pro.api.roles_api.users_container") as mock_role_users, \
                patch("smart_invoice_pro.api.roles_permissions_api._send_invite_email",
                      return_value=True) as mock_send:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_users.query_items.return_value = [target]
            resp = client.post(f"/api/settings/users/{target['id']}/resend-invite",
                               headers=auth_headers(role="Admin"))
        return resp, mock_users, mock_send

    def test_resend_sends_new_link_and_old_one_stops_working(self, client):
        user, old_token = _invitee()
        resp, mock_users, mock_send = self._post(client, user)
        assert resp.status_code == 200
        saved = mock_users.upsert_item.call_args.args[0]
        new_token = mock_send.call_args.kwargs["invite_token"]
        assert saved["invite_token_hash"] == hash_token(new_token)
        assert saved["invite_token_hash"] != hash_token(old_token)

    def test_resend_refused_after_password_set(self, client):
        user, _ = _invitee(invite_pending=False)
        resp, _, mock_send = self._post(client, user)
        assert resp.status_code == 400
        mock_send.assert_not_called()

    def test_resend_refused_for_other_tenant(self, client):
        user, _ = _invitee(tenant_id=TENANT_B)
        resp, _, mock_send = self._post(client, user)
        assert resp.status_code == 403
        mock_send.assert_not_called()


class TestInviteEmail:

    def test_email_has_link_and_no_password(self):
        from smart_invoice_pro.api import roles_permissions_api as rp
        with patch("smart_invoice_pro.utils.email_client.email_configured", return_value=True), \
                patch("smart_invoice_pro.utils.email_client.get_email_client") as mock_client, \
                patch.dict("os.environ", {"APP_URL": "https://books.example.com/"}):
            ok = rp._send_invite_email("n@example.com", "New", "newbie", "TOKEN123", "Sales", TENANT_A)
        assert ok is True
        message = mock_client.return_value.begin_send.call_args.args[0]
        content = message["content"]
        assert "https://books.example.com/accept-invite?token=TOKEN123" in content["plainText"]
        assert "https://books.example.com/accept-invite?token=TOKEN123" in content["html"]
        assert "temporary password" not in content["plainText"].lower()
        assert "Temporary password" not in content["html"]

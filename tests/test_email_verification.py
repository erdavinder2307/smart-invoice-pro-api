"""Email verification: sign-up link, verify, resend, and the invoice gate."""
import datetime
from unittest.mock import patch

from smart_invoice_pro.utils import email_verification as ev
from tests.conftest import USER_A, TENANT_A, auth_headers


def _unverified_user(**extra):
    user = {"id": USER_A, "userid": USER_A, "tenant_id": TENANT_A,
            "email": "owner@example.com", "name": "Owner"}
    ev.issue_token(user, now=datetime.datetime.utcnow() - datetime.timedelta(minutes=5))
    user.update(extra)
    return user


class TestSignUpSendsLink:

    @patch("smart_invoice_pro.utils.email_verification.send_verification_email", return_value=True)
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_sign_up_is_unverified_and_stores_only_a_hash(self, mock_users, mock_send, client):
        resp = client.post("/api/auth/register",
                           json={"username": "owner@example.com", "password": "secret123"})
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["user"]["email_verified"] is False
        assert body["verification_email_sent"] is True

        saved = mock_users.create_item.call_args.kwargs["body"]
        sent_token = mock_send.call_args.args[2]
        assert saved["email_verified"] is False
        assert saved["email_verification_hash"] == ev.hash_token(sent_token)
        assert sent_token not in str(saved)
        assert mock_send.call_args.args[0] == "owner@example.com"

    @patch("smart_invoice_pro.api.routes.users_container")
    def test_sign_up_without_email_is_rejected(self, mock_users, client):
        resp = client.post("/api/auth/register", json={"username": "noemail", "password": "secret123"})
        assert resp.status_code == 400
        mock_users.create_item.assert_not_called()

    @patch("smart_invoice_pro.utils.email_verification.send_verification_email", return_value=False)
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_sign_up_succeeds_when_email_cannot_be_sent(self, mock_users, mock_send, client):
        resp = client.post("/api/auth/register",
                           json={"username": "owner@example.com", "password": "secret123"})
        assert resp.status_code == 201
        assert resp.get_json()["verification_email_sent"] is False


class TestVerifyEmail:

    def test_verify_route_needs_no_sign_in(self, client):
        with patch("smart_invoice_pro.utils.email_verification.users_container") as mock_users:
            mock_users.query_items.return_value = []
            resp = client.post("/api/auth/verify-email", json={"token": "x"})
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_valid_token_verifies_and_is_single_use(self, mock_ev_users, mock_route_users, client):
        user = {"id": USER_A, "tenant_id": TENANT_A, "email": "owner@example.com"}
        token = ev.issue_token(user)
        mock_ev_users.query_items.return_value = [user]

        resp = client.post("/api/auth/verify-email", json={"token": token})
        assert resp.status_code == 200
        saved = mock_route_users.replace_item.call_args.kwargs["body"]
        assert saved["email_verified"] is True
        assert "email_verification_hash" not in saved

        query_value = mock_ev_users.query_items.call_args.kwargs["parameters"][0]["value"]
        assert query_value == ev.hash_token(token)

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_expired_token_is_refused(self, mock_ev_users, mock_route_users, client):
        user = {"id": USER_A, "tenant_id": TENANT_A}
        token = ev.issue_token(user, now=datetime.datetime.utcnow() - ev.TOKEN_TTL - datetime.timedelta(minutes=1))
        mock_ev_users.query_items.return_value = [user]

        resp = client.post("/api/auth/verify-email", json={"token": token})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "TOKEN_EXPIRED"
        mock_route_users.replace_item.assert_not_called()

    def test_empty_token_is_refused(self, client):
        resp = client.post("/api/auth/verify-email", json={})
        assert resp.status_code == 400


class TestResend:

    @patch("smart_invoice_pro.utils.email_verification.send_verification_email", return_value=True)
    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_resend_replaces_the_old_link(self, mock_ev_users, mock_route_users, mock_send, client):
        user = _unverified_user()
        old_hash = user["email_verification_hash"]
        mock_ev_users.read_item.return_value = user

        resp = client.post("/api/auth/resend-verification", headers=auth_headers())
        assert resp.status_code == 200
        saved = mock_route_users.replace_item.call_args.kwargs["body"]
        assert saved["email_verification_hash"] != old_hash
        assert saved["email_verification_hash"] == ev.hash_token(mock_send.call_args.args[2])

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_resend_is_rate_limited(self, mock_ev_users, client):
        user = _unverified_user(email_verification_sent_at=datetime.datetime.utcnow().isoformat())
        mock_ev_users.read_item.return_value = user
        resp = client.post("/api/auth/resend-verification", headers=auth_headers())
        assert resp.status_code == 429

    def test_resend_needs_sign_in(self, client):
        resp = client.post("/api/auth/resend-verification")
        assert resp.status_code == 401

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_resend_for_verified_user_sends_nothing(self, mock_ev_users, client):
        mock_ev_users.read_item.return_value = {"id": USER_A, "email_verified": True}
        with patch("smart_invoice_pro.utils.email_verification.send_verification_email") as mock_send:
            resp = client.post("/api/auth/resend-verification", headers=auth_headers())
        assert resp.status_code == 200
        mock_send.assert_not_called()


class TestInvoiceGate:

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_unverified_user_cannot_create_invoice(self, mock_ev_users, client):
        mock_ev_users.read_item.return_value = _unverified_user()
        resp = client.post("/api/invoices", headers=auth_headers(role="Admin"),
                           json={"customer_id": "c1", "items": []})
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "EMAIL_NOT_VERIFIED"

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_unverified_user_cannot_convert_quote_to_invoice(self, mock_ev_users, client):
        mock_ev_users.read_item.return_value = _unverified_user()
        resp = client.post("/api/quotes/q1/convert", headers=auth_headers(role="Admin"),
                           json={"convert_to": "invoice"})
        assert resp.status_code == 403

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_unverified_user_cannot_bulk_convert_quotes(self, mock_ev_users, client):
        mock_ev_users.read_item.return_value = _unverified_user()
        resp = client.post("/api/quotes/bulk-archive", headers=auth_headers(role="Admin"),
                           json={"action": "convert_to_invoice", "ids": ["q1"]})
        assert resp.status_code == 403

    @patch("smart_invoice_pro.utils.email_verification.users_container")
    def test_unverified_user_cannot_convert_sales_order(self, mock_ev_users, client):
        mock_ev_users.read_item.return_value = _unverified_user()
        resp = client.post("/api/sales-orders/so1/convert-invoice", headers=auth_headers(role="Admin"),
                           json={})
        assert resp.status_code == 403

    def test_accounts_from_before_verification_are_not_blocked(self):
        assert ev.is_verified({"id": "old-user"}) is True
        assert ev.is_verified({"email_verified": True}) is True
        assert ev.is_verified({"email_verified": False}) is False


class TestLoginReportsStatus:

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_says_unverified(self, mock_users, mock_refresh, client):
        from werkzeug.security import generate_password_hash
        user = _unverified_user(username="owner@example.com",
                                password=generate_password_hash("secret123", method="pbkdf2:sha256"),
                                role="Admin")
        mock_users.query_items.return_value = [user]
        resp = client.post("/api/auth/login",
                           json={"username": "owner@example.com", "password": "secret123"})
        assert resp.status_code == 200
        assert resp.get_json()["user"]["email_verified"] is False

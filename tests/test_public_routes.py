"""Which /api routes answer without a staff token (SE-4089).

The public list is written out here on purpose: making another route public
means changing this test as well as auth_middleware.py.
"""
import re
from unittest.mock import MagicMock, patch

import pytest


# (rule, method) pairs that work without a staff token.
PUBLIC_ROUTES = {
    ("/api/auth/login", "POST"),
    ("/api/auth/register", "POST"),
    ("/api/auth/refresh", "POST"),
    ("/api/auth/demo-login", "POST"),
    ("/api/auth/demo-roles", "GET"),
    ("/api/ping", "GET"),
    ("/api/payments/webhook", "POST"),
    ("/api/contact", "POST"),
    ("/api/customer/login", "POST"),
    ("/api/portal/invoice/<token>", "GET"),
}

_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")


def _sample_url(rule):
    return _PARAM.sub(lambda m: "1" if m.group(1) in ("int", "float") else "sample-1", rule)


def _protected_endpoints(app):
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api"):
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if (rule.rule, method) not in PUBLIC_ROUTES:
                yield rule.rule, method


def test_public_list_matches_registered_routes(app):
    registered = {
        (rule.rule, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods
    }
    missing = PUBLIC_ROUTES - registered
    assert not missing, f"public routes not registered: {sorted(missing)}"


def test_every_other_api_route_needs_a_token(app, client):
    wrong = []
    for rule, method in _protected_endpoints(app):
        resp = client.open(_sample_url(rule), method=method)
        # /api/cron/* is guarded by the cron secret instead of a staff token.
        expected = 403 if rule.startswith("/api/cron/") else 401
        if resp.status_code != expected:
            wrong.append(f"{method} {rule} -> {resp.status_code}")
    assert not wrong, "routes answering without a staff token:\n" + "\n".join(wrong)


class TestPortalInvoice:
    """GET /api/portal/invoice/<token> without a staff token."""

    def test_unknown_or_revoked_token_is_404(self, client):
        with patch("smart_invoice_pro.api.invoices.invoices_container") as container:
            container.query_items.return_value = []
            resp = client.get("/api/portal/invoice/no-such-token")
        assert resp.status_code == 404

    def test_known_token_returns_only_that_invoice(self, client):
        invoice = {
            "id": "inv-1",
            "tenant_id": "tenant-x",
            "invoice_number": "INV-0001",
            "customer_name": "Test Customer",
            "total_amount": 118,
            "portal_token": "tok-123",
            "internal_note": "staff only",
            "created_by": "user-1",
        }
        with patch("smart_invoice_pro.api.invoices.invoices_container") as container, \
             patch("smart_invoice_pro.api.organization_profile_api._get_profile", return_value={}):
            container.query_items.return_value = [invoice]
            resp = client.get("/api/portal/invoice/tok-123")
            kwargs = container.query_items.call_args.kwargs

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["invoice_number"] == "INV-0001"
        assert "tenant_id" not in body
        assert "internal_note" not in body
        assert "created_by" not in body
        assert kwargs["parameters"] == [{"name": "@token", "value": "tok-123"}]

    def test_error_details_are_not_returned(self, client):
        with patch("smart_invoice_pro.api.invoices.invoices_container") as container:
            container.query_items.side_effect = RuntimeError("db host details")
            resp = client.get("/api/portal/invoice/tok-123")
        assert resp.status_code == 500
        assert "db host" not in resp.get_data(as_text=True)


class TestContactWithoutToken:
    """POST /api/contact keeps its validation and works without a staff token."""

    def test_missing_field_is_still_rejected(self, client):
        resp = client.post("/api/contact", json={"name": "A", "email": "a@example.com"})
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", None)
    def test_valid_message_is_accepted(self, client):
        resp = client.post("/api/contact", json={
            "name": "A", "email": "a@example.com", "subject": "Hi", "message": "Hello",
        })
        assert resp.status_code == 200


class TestCustomerLoginWithoutToken:
    """POST /api/customer/login without a staff token."""

    def test_unknown_customer_gets_invalid_login(self, client):
        with patch("smart_invoice_pro.api.customers_api.customers_container") as container:
            container.query_items.return_value = []
            resp = client.post("/api/customer/login", json={
                "email": "x' OR '1'='1", "password": "pw",
            })
            kwargs = container.query_items.call_args.kwargs

        assert resp.status_code == 401
        assert resp.get_json()["message"] == "Invalid email or password."
        # The email is passed as a query parameter, never pasted into the SQL text.
        assert "@email" in kwargs["query"]
        assert kwargs["parameters"] == [{"name": "@email", "value": "x' OR '1'='1"}]

    @pytest.mark.parametrize("payload", [{}, {"email": "a@example.com"}])
    def test_missing_fields_are_rejected(self, client, payload):
        resp = client.post("/api/customer/login", json=payload)
        assert resp.status_code == 400


class TestPublicRouteHardening:
    """Rate limits and input handling on the routes that no longer need a staff token."""

    def test_customer_login_is_rate_limited(self, client):
        with patch("smart_invoice_pro.api.customers_api.customers_container") as container:
            container.query_items.return_value = []
            codes = [
                client.post("/api/customer/login", json={"email": "a@example.com", "password": "pw"}).status_code
                for _ in range(6)
            ]
            last = client.post("/api/customer/login", json={"email": "a@example.com", "password": "pw"})
        assert codes[:5] == [401] * 5
        assert codes[5] == 429
        assert last.status_code == 429
        assert last.is_json
        assert last.get_json()["message"] == "Too many requests. Please try again later."

    def test_customer_without_password_gets_the_same_answer(self, client):
        with patch("smart_invoice_pro.api.customers_api.customers_container") as container:
            container.query_items.return_value = [{"id": "c1", "email": "a@example.com", "name": "A"}]
            resp = client.post("/api/customer/login", json={"email": "a@example.com", "password": "pw"})
        assert resp.status_code == 401
        assert resp.get_json()["message"] == "Invalid email or password."

    def test_customer_login_rejects_non_object_json(self, client):
        resp = client.post("/api/customer/login", json=["a@example.com", "pw"])
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", None)
    def test_contact_is_rate_limited(self, client):
        payload = {"name": "A", "email": "a@example.com", "subject": "Hi", "message": "Hello"}
        resps = [client.post("/api/contact", json=payload) for _ in range(4)]
        assert [r.status_code for r in resps] == [200, 200, 200, 429]
        assert resps[3].is_json
        assert resps[3].get_json()["message"] == "Too many requests. Please try again later."

    def test_contact_rejects_non_object_json(self, client):
        resp = client.post("/api/contact", json=["not", "an", "object"])
        assert resp.status_code == 400

    def test_contact_rejects_overlong_message(self, client):
        resp = client.post("/api/contact", json={
            "name": "A", "email": "a@example.com", "subject": "Hi", "message": "x" * 5001,
        })
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.contact_api.EmailClient")
    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "endpoint=https://real.com;accesskey=REALKEY")
    def test_contact_email_escapes_user_input(self, mock_email_cls, client):
        mock_client = MagicMock()
        mock_client.begin_send.return_value.result.return_value = {"id": "msg-1"}
        mock_email_cls.from_connection_string.return_value = mock_client

        resp = client.post("/api/contact", json={
            "name": "<b>Eve</b>", "email": "e@example.com", "subject": "Hi\r\nBcc: x@example.com",
            "message": '<a href="https://evil.example">click</a>',
        })

        assert resp.status_code == 200
        content = mock_client.begin_send.call_args.args[0]["content"]
        assert "<b>Eve</b>" not in content["html"]
        assert "&lt;b&gt;Eve&lt;/b&gt;" in content["html"]
        assert "<a href" not in content["html"]
        assert "\r" not in content["subject"] and "\n" not in content["subject"]

    @patch("smart_invoice_pro.api.contact_api.EmailClient")
    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "endpoint=https://real.com;accesskey=REALKEY")
    def test_contact_failure_hides_error_text(self, mock_email_cls, client):
        mock_email_cls.from_connection_string.side_effect = Exception("internal host detail")
        resp = client.post("/api/contact", json={
            "name": "A", "email": "a@example.com", "subject": "Hi", "message": "Hello",
        })
        assert resp.status_code == 500
        assert "internal host detail" not in resp.get_data(as_text=True)

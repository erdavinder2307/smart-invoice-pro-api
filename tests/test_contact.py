"""Tests for contact_api.py – contact form email endpoint."""
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import TENANT_A, USER_A


VALID_CONTACT = {
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "1234567890",
    "subject": "Inquiry",
    "message": "I need info about your product.",
}


class TestSendMessage:
    """POST /contact"""

    def test_missing_json_body(self, client, headers_a):
        resp = client.post(
            "/api/contact",
            data="not json",
            content_type="text/plain",
            headers={"Authorization": headers_a["Authorization"]},
        )
        assert resp.status_code == 400
        assert "JSON" in resp.get_json()["error"]

    @pytest.mark.parametrize("field", ["name", "email", "subject", "message"])
    def test_missing_required_field(self, field, client, headers_a):
        payload = {**VALID_CONTACT}
        del payload[field]
        resp = client.post("/api/contact", json=payload, headers=headers_a)
        assert resp.status_code == 400
        assert field in resp.get_json()["error"]

    @pytest.mark.parametrize("field", ["name", "email", "subject", "message"])
    def test_empty_required_field(self, field, client, headers_a):
        payload = {**VALID_CONTACT, field: ""}
        resp = client.post("/api/contact", json=payload, headers=headers_a)
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "endpoint=https://x;accesskey=YOUR_KEY")
    def test_invalid_connection_string_simulation(self, client, headers_a):
        """When connection string contains YOUR_KEY, returns simulation message."""
        resp = client.post("/api/contact", json=VALID_CONTACT, headers=headers_a)
        assert resp.status_code == 200
        assert "simulation" in resp.get_json()["message"].lower()

    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", None)
    def test_no_connection_string_simulation(self, client, headers_a):
        resp = client.post("/api/contact", json=VALID_CONTACT, headers=headers_a)
        assert resp.status_code == 200
        assert "simulation" in resp.get_json()["message"].lower()

    @patch("smart_invoice_pro.api.contact_api.EmailClient")
    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "endpoint=https://real.com;accesskey=REALKEY")
    def test_email_sent_successfully(self, mock_email_cls, client, headers_a):
        mock_client = MagicMock()
        mock_poller = MagicMock()
        mock_poller.result.return_value = {"id": "msg-1"}
        mock_client.begin_send.return_value = mock_poller
        mock_email_cls.from_connection_string.return_value = mock_client

        resp = client.post("/api/contact", json=VALID_CONTACT, headers=headers_a)
        assert resp.status_code == 200
        assert "successfully" in resp.get_json()["message"].lower()
        mock_client.begin_send.assert_called_once()

    @patch("smart_invoice_pro.api.contact_api.EmailClient")
    @patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "endpoint=https://real.com;accesskey=REALKEY")
    def test_email_send_failure(self, mock_email_cls, client, headers_a):
        mock_email_cls.from_connection_string.side_effect = Exception("Connection refused")
        resp = client.post("/api/contact", json=VALID_CONTACT, headers=headers_a)
        assert resp.status_code == 500
        assert "Failed" in resp.get_json()["error"]

    def test_phone_defaults_to_na(self, client, headers_a):
        """When phone is omitted, the code defaults it to 'N/A' (no error)."""
        payload = {k: v for k, v in VALID_CONTACT.items() if k != "phone"}
        with patch("smart_invoice_pro.api.contact_api.CONNECTION_STRING", "x=YOUR_KEY"):
            resp = client.post("/api/contact", json=payload, headers=headers_a)
        assert resp.status_code == 200

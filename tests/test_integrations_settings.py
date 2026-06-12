"""
Tests for Integrations Settings API.
GET/PUT /api/settings/integrations
POST    /api/settings/integrations/test-email
GET     /api/settings/integrations/webhook-logs
"""
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A


STORED_DOC = {
    "id": f"{TENANT_A}:integrations_settings",
    "type": "integrations_settings",
    "tenant_id": TENANT_A,
    "email": {
        "provider": "azure",
        "sender_email": "bills@example.com",
        "sender_name": "Example Corp",
        "enabled": True,
    },
    "webhooks": [
        {
            "id": "wh-1",
            "url": "https://example.com/hook",
            "events": ["invoice.created"],
            "active": True,
            "secret": "mysecret123",
        },
    ],
}


class TestGetIntegrationsSettings:
    """GET /api/settings/integrations"""

    def test_returns_defaults_when_none(self, client, headers_a):
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "payments" not in data
        assert "banking" not in data
        assert "email" in data
        assert "webhooks" in data
        assert data["email"]["provider"] == "azure"

    def test_secrets_are_masked(self, client, headers_a):
        """Webhook signing secrets must be masked in GET response."""
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_DOC.copy()]
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        webhook = data["webhooks"][0]
        assert "\u2022\u2022\u2022\u2022" in (webhook.get("secret") or "")
        assert "mysecret123" not in (webhook.get("secret") or "")

    def test_strips_cosmos_internal_fields(self, client, headers_a):
        stored = {**STORED_DOC, "_rid": "x", "_self": "y", "_etag": "z", "_ts": 1}
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "_rid" not in data
        assert "_ts" not in data

    def test_returns_sender_name(self, client, headers_a):
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_DOC.copy()]
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["email"]["sender_name"] == "Example Corp"


class TestSaveIntegrationsSettings:
    """PUT /api/settings/integrations"""

    def test_update_email_sender_name(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "email": {"sender_email": "new@example.com", "sender_name": "New Corp", "enabled": True}
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert data["email"]["sender_email"] == "new@example.com"
        assert data["email"]["sender_name"] == "New Corp"

    def test_update_email(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "email": {"sender_email": "updated@example.com"}
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert data["email"]["sender_email"] == "updated@example.com"

    def test_masked_secret_not_overwritten(self, client, headers_a):
        """Sending a masked placeholder back must NOT overwrite the real webhook secret."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [{"id": "wh-1", "url": "https://example.com/hook",
                               "events": ["invoice.created"], "active": True,
                               "secret": "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022ret"}]
            }, headers=headers_a)
        assert resp.status_code == 200
        upserted = mock_ctr.upsert_item.call_args[1]["body"]
        assert upserted["webhooks"][0]["secret"] == "mysecret123"

    def test_real_webhook_secret_update(self, client, headers_a):
        """Sending a real (non-masked) webhook secret value should update it."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [{"id": "wh-1", "url": "https://example.com/hook",
                               "events": ["invoice.created"], "active": True,
                               "secret": "brand_new_secret_xyz"}]
            }, headers=headers_a)
        assert resp.status_code == 200
        upserted = mock_ctr.upsert_item.call_args[1]["body"]
        assert upserted["webhooks"][0]["secret"] == "brand_new_secret_xyz"

    def test_add_webhook(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [
                    {"url": "https://hooks.example.com/new", "events": ["invoice.paid"], "active": True},
                ]
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert len(data["webhooks"]) == 1
        assert data["webhooks"][0]["url"] == "https://hooks.example.com/new"
        assert data["webhooks"][0]["id"]

    def test_invalid_webhook_url(self, client, headers_a):
        """Non-https webhook URL must be rejected with 400."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [{"url": "ftp://bad.com", "events": ["invoice.created"]}]
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "https" in resp.get_json()["error"].lower()

    def test_unsupported_webhook_event(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [{"url": "https://ok.com/hook", "events": ["no.such.event"]}]
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "unsupported event" in resp.get_json()["error"].lower()

    def test_no_data_returns_400(self, client, headers_a):
        resp = client.put("/api/settings/integrations",
                          data="", content_type="application/json", headers=headers_a)
        assert resp.status_code == 400

    def test_payments_and_banking_fields_ignored(self, client, headers_a):
        """Sending legacy payments/banking fields should not cause errors."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "payments": {"enabled": True, "api_key": "sk_test_123"},
                "banking": {"enabled": True, "provider": "icici"},
                "email": {"sender_email": "ok@example.com"},
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert "payments" not in data
        assert "banking" not in data


class TestTestEmailEndpoint:
    """POST /api/settings/integrations/test-email"""

    def test_missing_to_field_returns_400(self, client, headers_a):
        resp = client.post("/api/settings/integrations/test-email",
                           json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_endpoint_exists_and_handles_request(self, client, headers_a):
        """Endpoint must exist and return a non-404 response (200 or 500 depending on ACS config)."""
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_DOC.copy()]
            # Mock the Azure EmailClient used inside the handler
            with patch("azure.communication.email.EmailClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.from_connection_string.return_value = mock_client
                mock_client.begin_send.return_value = MagicMock(result=MagicMock(return_value={}))
                resp = client.post("/api/settings/integrations/test-email",
                                   json={"to": "recipient@example.com"},
                                   headers=headers_a)
        assert resp.status_code != 404


class TestWebhookLogsEndpoint:
    """GET /api/settings/integrations/webhook-logs"""

    def test_returns_logs(self, client, headers_a):
        # get_webhook_logs does a local re-import, so patch at the cosmos_client source
        sample_log = {
            "id": "log-1",
            "tenant_id": TENANT_A,
            "event": "invoice.created",
            "url": "https://example.com/hook",
            "success": True,
            "status_code": 200,
            "delivered_at": "2024-01-01T00:00:00",
        }
        mock_ctr = MagicMock()
        mock_ctr.query_items.return_value = [sample_log]
        with patch("smart_invoice_pro.utils.cosmos_client.webhook_logs_container", mock_ctr):
            resp = client.get("/api/settings/integrations/webhook-logs", headers=headers_a)
        assert resp.status_code == 200
        logs = resp.get_json()
        assert isinstance(logs, list)
        assert logs[0]["event"] == "invoice.created"

    def test_returns_empty_list_when_no_logs(self, client, headers_a):
        mock_ctr = MagicMock()
        mock_ctr.query_items.return_value = []
        with patch("smart_invoice_pro.utils.cosmos_client.webhook_logs_container", mock_ctr):
            resp = client.get("/api/settings/integrations/webhook-logs", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json() == []

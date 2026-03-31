"""
Tests for Integrations Settings API.
GET/PUT /api/settings/integrations
"""
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A


STORED_DOC = {
    "id": f"{TENANT_A}:integrations_settings",
    "type": "integrations_settings",
    "tenant_id": TENANT_A,
    "payments": {
        "provider": "zoho",
        "enabled": True,
        "api_key": "sk_live_abc123secret",
        "webhook_secret": "whsec_xyz789secret",
        "status": "connected",
    },
    "banking": {"enabled": False, "provider": None},
    "email": {"provider": "azure", "sender_email": "test@example.com", "enabled": True},
    "webhooks": [
        {"id": "wh-1", "url": "https://example.com/hook", "events": ["invoice.created"], "active": True},
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
        assert data["payments"]["status"] == "disconnected"

    def test_secrets_are_masked(self, client, headers_a):
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_DOC.copy()]
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        # API key should be masked — starts with dots, ends with last 4
        assert "••••" in (data["payments"]["api_key"] or "")
        assert "••••" in (data["payments"]["webhook_secret"] or "")
        # But original secrets not exposed
        assert "abc123secret" not in (data["payments"]["api_key"] or "")

    def test_strips_cosmos_internal_fields(self, client, headers_a):
        stored = {**STORED_DOC, "_rid": "x", "_self": "y", "_etag": "z", "_ts": 1}
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.get("/api/settings/integrations", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "_rid" not in data
        assert "_ts" not in data


class TestSaveIntegrationsSettings:
    """PUT /api/settings/integrations"""

    def test_update_payments_enabled(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations",
                              json={"payments": {"enabled": False}}, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert data["payments"]["status"] == "disconnected"

    def test_masked_secret_not_overwritten(self, client, headers_a):
        """Sending masked placeholder back should NOT overwrite the real secret."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "payments": {"api_key": "••••••••••cret"}
            }, headers=headers_a)
        assert resp.status_code == 200
        # The actual stored value should remain unchanged
        upserted = mock_ctr.upsert_item.call_args[1]["body"]
        assert upserted["payments"]["api_key"] == "sk_live_abc123secret"

    def test_real_secret_update(self, client, headers_a):
        """Sending a real (non-masked) value should update the secret."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "payments": {"api_key": "new_secret_key_12345"}
            }, headers=headers_a)
        assert resp.status_code == 200
        upserted = mock_ctr.upsert_item.call_args[1]["body"]
        assert upserted["payments"]["api_key"] == "new_secret_key_12345"

    def test_update_banking(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "banking": {"enabled": True, "provider": "icici"}
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert data["banking"]["enabled"] is True
        assert data["banking"]["provider"] == "icici"

    def test_update_email(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "email": {"sender_email": "new@example.com"}
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()["settings"]
        assert data["email"]["sender_email"] == "new@example.com"

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
        # Auto-assigned UUID
        assert data["webhooks"][0]["id"]

    def test_invalid_webhook_url(self, client, headers_a):
        import copy
        stored = copy.deepcopy(STORED_DOC)
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "webhooks": [{"url": "ftp://bad.com", "events": ["invoice.created"]}]
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "invalid webhook url" in resp.get_json()["error"].lower()

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

    def test_payments_status_derived_correctly(self, client, headers_a):
        """enabled=True + api_key → connected; enabled=True + no key → pending."""
        import copy
        stored = copy.deepcopy(STORED_DOC)
        stored["payments"]["api_key"] = None
        with patch("smart_invoice_pro.api.integrations_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.put("/api/settings/integrations", json={
                "payments": {"enabled": True}
            }, headers=headers_a)
        assert resp.status_code == 200
        upserted = mock_ctr.upsert_item.call_args[1]["body"]
        assert upserted["payments"]["status"] == "pending"

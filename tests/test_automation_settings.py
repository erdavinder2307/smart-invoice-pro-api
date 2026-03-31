"""
Tests for Automation Settings API.
GET/PUT /api/settings/automation
"""
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A


class TestGetAutomationSettings:
    """GET /api/settings/automation"""

    def test_returns_defaults_when_none_stored(self, client, headers_a):
        """If no doc in DB, returns default config."""
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/settings/automation", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["email_enabled"] is True
        assert len(data["payment_reminders"]) == 3

    def test_returns_stored_config(self, client, headers_a):
        """Returns existing doc if present."""
        stored = {
            "id": f"{TENANT_A}:automation_settings",
            "type": "automation_settings",
            "tenant_id": TENANT_A,
            "email_enabled": False,
            "payment_reminders": [
                {"type": "before_due", "days": 5, "enabled": True},
            ],
        }
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.get("/api/settings/automation", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["email_enabled"] is False
        assert len(data["payment_reminders"]) == 1

    def test_strips_internal_fields(self, client, headers_a):
        """Cosmos internal fields (_rid, _self, etc.) are stripped."""
        stored = {
            "id": f"{TENANT_A}:automation_settings",
            "tenant_id": TENANT_A,
            "email_enabled": True,
            "payment_reminders": [],
            "_rid": "xxx", "_self": "yyy", "_etag": "zzz",
        }
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = [stored]
            resp = client.get("/api/settings/automation", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "_rid" not in data
        assert "_self" not in data


class TestSaveAutomationSettings:
    """PUT /api/settings/automation"""

    def test_update_email_enabled(self, client, headers_a):
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/settings/automation",
                              json={"email_enabled": False}, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["settings"]["email_enabled"] is False
        mock_ctr.upsert_item.assert_called_once()

    def test_update_valid_reminders(self, client, headers_a):
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {
                "payment_reminders": [
                    {"type": "before_due", "days": 7, "enabled": True},
                    {"type": "on_due", "days": 0, "enabled": True},
                    {"type": "after_due", "days": 5, "enabled": False},
                ]
            }
            resp = client.put("/api/settings/automation", json=payload, headers=headers_a)
        assert resp.status_code == 200
        reminders = resp.get_json()["settings"]["payment_reminders"]
        assert len(reminders) == 3
        assert reminders[0]["days"] == 7

    def test_invalid_reminder_type(self, client, headers_a):
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {
                "payment_reminders": [
                    {"type": "invalid_type", "days": 3, "enabled": True},
                ]
            }
            resp = client.put("/api/settings/automation", json=payload, headers=headers_a)
        assert resp.status_code == 400
        assert "invalid" in resp.get_json()["error"].lower()

    def test_invalid_days_too_high(self, client, headers_a):
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {
                "payment_reminders": [
                    {"type": "before_due", "days": 100, "enabled": True},
                ]
            }
            resp = client.put("/api/settings/automation", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_duplicate_reminder_type(self, client, headers_a):
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {
                "payment_reminders": [
                    {"type": "before_due", "days": 3, "enabled": True},
                    {"type": "before_due", "days": 5, "enabled": True},
                ]
            }
            resp = client.put("/api/settings/automation", json=payload, headers=headers_a)
        assert resp.status_code == 400
        assert "duplicate" in resp.get_json()["error"].lower()

    def test_no_data_returns_400(self, client, headers_a):
        resp = client.put("/api/settings/automation",
                          data="", content_type="application/json", headers=headers_a)
        assert resp.status_code == 400

    def test_on_due_days_forced_to_zero(self, client, headers_a):
        """on_due type always forces days=0 regardless of input."""
        with patch("smart_invoice_pro.api.automation_settings_api.settings_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {
                "payment_reminders": [
                    {"type": "on_due", "days": 5, "enabled": True},
                ]
            }
            resp = client.put("/api/settings/automation", json=payload, headers=headers_a)
        assert resp.status_code == 200
        reminders = resp.get_json()["settings"]["payment_reminders"]
        assert reminders[0]["days"] == 0

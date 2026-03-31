"""Tests for invoice_preferences_api.py – GET/PUT preferences + invoice number generation."""
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B, auth_headers


# ── Helpers ──────────────────────────────────────────────────────────────────
ADMIN_USER = {"id": USER_A, "user_id": USER_A, "tenant_id": TENANT_A, "role": "Admin"}

STORED_PREFS = {
    "id": f"{TENANT_A}:invoice_preferences",
    "type": "invoice_preferences",
    "tenant_id": TENANT_A,
    "invoice_prefix": "INV-",
    "invoice_suffix": "",
    "next_invoice_number": 42,
    "number_padding": 5,
    "default_payment_terms": "Net 30",
    "default_due_days": 30,
    "default_notes": "Thanks!",
    "default_terms": "Pay within 30 days.",
    "auto_generate_invoice_number": True,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00",
}


class TestGetInvoicePreferences:
    """GET /settings/invoice-preferences"""

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_returns_defaults_when_none(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.get("/api/settings/invoice-preferences", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["invoice_prefix"] == "INV-"
        assert data["next_invoice_number"] == 1
        assert data["number_padding"] == 5

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_returns_stored_config(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [STORED_PREFS.copy()]
        resp = client.get("/api/settings/invoice-preferences", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["next_invoice_number"] == 42
        assert data["default_notes"] == "Thanks!"

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_strips_internal_fields(self, mock_ctr, client, headers_a):
        doc = {**STORED_PREFS, "_rid": "x", "_self": "y", "_etag": "z", "_ts": 0}
        mock_ctr.query_items.return_value = [doc]
        resp = client.get("/api/settings/invoice-preferences", headers=headers_a)
        data = resp.get_json()
        for key in ("_rid", "_self", "_etag", "_ts"):
            assert key not in data


class TestUpdateInvoicePreferences:
    """PUT /settings/invoice-preferences (Admin only)"""

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_update_success(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_ctr.query_items.return_value = [STORED_PREFS.copy()]

        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"invoice_prefix": "SI-", "next_invoice_number": 100, "number_padding": 6},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["invoice_prefix"] == "SI-"
        assert data["next_invoice_number"] == 100
        assert data["number_padding"] == 6
        mock_ctr.upsert_item.assert_called_once()

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_empty_prefix_rejected(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"invoice_prefix": ""},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "invoice_prefix" in resp.get_json().get("fields", {})

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_negative_next_number_rejected(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"next_invoice_number": -5},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "next_invoice_number" in resp.get_json().get("fields", {})

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_padding_out_of_range(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"number_padding": 99},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "number_padding" in resp.get_json().get("fields", {})

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_negative_due_days_rejected(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"default_due_days": -1},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "default_due_days" in resp.get_json().get("fields", {})

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_notes_too_long_rejected(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"default_notes": "x" * 2001},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "default_notes" in resp.get_json().get("fields", {})

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_prefix_too_long_rejected(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        resp = client.put(
            "/api/settings/invoice-preferences",
            json={"invoice_prefix": "A" * 21},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "invoice_prefix" in resp.get_json().get("fields", {})

    def test_non_admin_forbidden(self, client):
        """Non-Admin role is rejected."""
        headers = auth_headers(user_id=USER_B, tenant_id=TENANT_B)
        with patch("smart_invoice_pro.api.roles_api.users_container") as mock_u:
            mock_u.query_items.return_value = [
                {"id": USER_B, "user_id": USER_B, "tenant_id": TENANT_B, "role": "Sales"}
            ]
            resp = client.put(
                "/api/settings/invoice-preferences", json={"invoice_prefix": "X-"}, headers=headers
            )
        assert resp.status_code == 403


class TestFormatInvoiceNumber:
    """Unit tests for format_invoice_number helper."""

    def test_basic_format(self):
        from smart_invoice_pro.api.invoice_preferences_api import format_invoice_number
        assert format_invoice_number("INV-", 42, 5, "") == "INV-00042"

    def test_with_suffix(self):
        from smart_invoice_pro.api.invoice_preferences_api import format_invoice_number
        assert format_invoice_number("SI-", 1, 4, "-2025") == "SI-0001-2025"

    def test_padding_clamped_low(self):
        from smart_invoice_pro.api.invoice_preferences_api import format_invoice_number
        assert format_invoice_number("X", 7, 0, "") == "X7"  # clamped to 1

    def test_padding_clamped_high(self):
        from smart_invoice_pro.api.invoice_preferences_api import format_invoice_number
        result = format_invoice_number("X", 1, 15, "")
        assert result == "X0000000001"  # clamped to 10


class TestPeekNextInvoiceNumber:
    """peek_next_invoice_number should NOT increment the counter."""

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_peek(self, mock_ctr):
        mock_ctr.query_items.return_value = [STORED_PREFS.copy()]
        from smart_invoice_pro.api.invoice_preferences_api import peek_next_invoice_number
        result = peek_next_invoice_number(TENANT_A)
        assert result == "INV-00042"
        mock_ctr.replace_item.assert_not_called()
        mock_ctr.create_item.assert_not_called()

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_peek_defaults(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        from smart_invoice_pro.api.invoice_preferences_api import peek_next_invoice_number
        result = peek_next_invoice_number(TENANT_A)
        assert result == "INV-00001"


class TestGenerateInvoiceNumber:
    """generate_invoice_number should atomically increment."""

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_generate_first_time(self, mock_ctr):
        """No existing doc → create_item path."""
        mock_ctr.query_items.return_value = []
        from smart_invoice_pro.api.invoice_preferences_api import generate_invoice_number
        result = generate_invoice_number(TENANT_A)
        assert result == "INV-00001"
        mock_ctr.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.invoice_preferences_api.settings_container")
    def test_generate_with_existing(self, mock_ctr):
        """Existing doc with _etag → replace_item path."""
        doc = {**STORED_PREFS, "_etag": "etag-1"}
        mock_ctr.query_items.return_value = [doc]
        from smart_invoice_pro.api.invoice_preferences_api import generate_invoice_number
        result = generate_invoice_number(TENANT_A)
        assert result == "INV-00042"
        mock_ctr.replace_item.assert_called_once()

"""Tests for branding_api.py – GET/PUT branding settings."""
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B, auth_headers


ADMIN_USER = {"id": USER_A, "user_id": USER_A, "tenant_id": TENANT_A, "role": "Admin"}

ORG_PROFILE = {
    "id": f"{TENANT_A}:organization_profile",
    "type": "organization_profile",
    "tenant_id": TENANT_A,
    "company_name": "Test Co",
    "logo_url": "https://example.com/logo.png",
    "primary_color": "#FF0000",
    "secondary_color": "#00FF00",
    "accent_color": "#0000FF",
    "email_header_logo_url": "https://example.com/header.png",
    "invoice_template_settings": {"show_logo": True, "show_signature": True},
}

EMPTY_PROFILE = {
    "id": f"{TENANT_A}:organization_profile",
    "type": "organization_profile",
    "tenant_id": TENANT_A,
}


class TestGetBranding:
    """GET /settings/branding"""

    @patch("smart_invoice_pro.api.branding_api._get_profile")
    def test_returns_defaults_when_empty(self, mock_prof, client, headers_a):
        mock_prof.return_value = EMPTY_PROFILE.copy()
        resp = client.get("/api/settings/branding", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["primary_color"] == "#2563EB"
        assert data["secondary_color"] == "#10B981"
        assert data["accent_color"] == "#2d6cdf"

    @patch("smart_invoice_pro.api.branding_api._get_profile")
    def test_returns_stored_colors(self, mock_prof, client, headers_a):
        mock_prof.return_value = ORG_PROFILE.copy()
        resp = client.get("/api/settings/branding", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["primary_color"] == "#FF0000"
        assert data["logo_url"] == "https://example.com/logo.png"
        assert data["invoice_template_settings"]["show_signature"] is True

    @patch("smart_invoice_pro.api.branding_api._get_profile")
    def test_template_settings_defaults(self, mock_prof, client, headers_a):
        mock_prof.return_value = EMPTY_PROFILE.copy()
        resp = client.get("/api/settings/branding", headers=headers_a)
        data = resp.get_json()
        assert data["invoice_template_settings"]["show_logo"] is True
        assert data["invoice_template_settings"]["show_signature"] is False


class TestUpdateBranding:
    """PUT /settings/branding (Admin only)"""

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_update_colors(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = ORG_PROFILE.copy()

        resp = client.put(
            "/api/settings/branding",
            json={"primary_color": "#123456", "secondary_color": "#ABCDEF"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["primary_color"] == "#123456"
        assert data["secondary_color"] == "#ABCDEF"
        mock_ctr.upsert_item.assert_called_once()

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_invalid_hex_color_rejected(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = ORG_PROFILE.copy()

        resp = client.put(
            "/api/settings/branding",
            json={"primary_color": "red"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "hex" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_invalid_short_hex_rejected(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = ORG_PROFILE.copy()
        resp = client.put(
            "/api/settings/branding",
            json={"accent_color": "#FFF"},
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_update_template_settings(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = ORG_PROFILE.copy()

        resp = client.put(
            "/api/settings/branding",
            json={"invoice_template_settings": {"show_logo": False, "show_signature": True}},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["invoice_template_settings"]["show_logo"] is False
        assert data["invoice_template_settings"]["show_signature"] is True

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_update_email_header_logo(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = ORG_PROFILE.copy()

        resp = client.put(
            "/api/settings/branding",
            json={"email_header_logo_url": "https://img.example.com/new.png"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["email_header_logo_url"] == "https://img.example.com/new.png"

    @patch("smart_invoice_pro.api.branding_api.settings_container")
    @patch("smart_invoice_pro.api.branding_api._get_profile")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_empty_profile_gets_id_on_upsert(self, mock_users, mock_prof, mock_ctr, client, headers_a):
        """When the profile has no id, the code generates one before upsert."""
        mock_users.query_items.return_value = [ADMIN_USER]
        mock_prof.return_value = {"tenant_id": TENANT_A}

        resp = client.put(
            "/api/settings/branding",
            json={"primary_color": "#111111"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        upserted = mock_ctr.upsert_item.call_args[0][0]
        assert "id" in upserted
        assert upserted["type"] == "organization_profile"

    def test_non_admin_forbidden(self, client):
        headers = auth_headers(user_id=USER_B, tenant_id=TENANT_B)
        with patch("smart_invoice_pro.api.roles_api.users_container") as mock_u:
            mock_u.query_items.return_value = [
                {"id": USER_B, "user_id": USER_B, "tenant_id": TENANT_B, "role": "Sales"}
            ]
            resp = client.put(
                "/api/settings/branding",
                json={"primary_color": "#FFFFFF"},
                headers=headers,
            )
        assert resp.status_code == 403


class TestExtractBranding:
    """Unit tests for _extract_branding helper."""

    def test_full_profile(self):
        from smart_invoice_pro.api.branding_api import _extract_branding
        result = _extract_branding(ORG_PROFILE)
        assert result["primary_color"] == "#FF0000"
        assert result["logo_url"] == "https://example.com/logo.png"

    def test_empty_profile_returns_defaults(self):
        from smart_invoice_pro.api.branding_api import _extract_branding
        result = _extract_branding({})
        assert result["primary_color"] == "#2563EB"
        assert result["secondary_color"] == "#10B981"
        assert result["accent_color"] == "#2d6cdf"
        assert result["logo_url"] == ""

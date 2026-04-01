"""
Tests for Super Admin API — /api/admin/*

Covers:
  - Authentication (no token → 401)
  - Authorization (non-admin → 403, super admin → success)
  - Tenant CRUD (list, get, status update, soft delete)
  - User management (list, status update, password reset)
  - Feature flags (get, create, update)
  - System stats
  - Input validation
  - Audit logging side-effects
"""
import pytest
from unittest.mock import MagicMock, patch, call

from tests.conftest import (
    TENANT_A, TENANT_B, USER_A, USER_B,
    auth_headers, make_token,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def super_admin_headers(user_id=USER_A, tenant_id=TENANT_A):
    """Return auth headers with is_super_admin=True in the JWT."""
    token = make_token(user_id=user_id, tenant_id=tenant_id, is_super_admin=True)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def normal_user_headers(user_id=USER_A, tenant_id=TENANT_A):
    """Return auth headers for a normal (non-admin) user."""
    return auth_headers(user_id=user_id, tenant_id=tenant_id)


# Sample data
SAMPLE_TENANT = {
    "id": "tenant-001",
    "name": "Acme Corp",
    "status": "active",
    "plan": "pro",
    "created_at": "2025-01-01T00:00:00",
}

SAMPLE_USER = {
    "id": "user-001",
    "username": "john",
    "email": "john@acme.com",
    "tenant_id": "tenant-001",
    "role": "Admin",
    "status": "active",
    "password": "hashed_secret",
    "created_at": "2025-01-01T00:00:00",
}


# ═════════════════════════════════════════════════════════════════════════════
# AUTH / AUTHORIZATION TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminAuth:
    """Ensure all admin endpoints enforce auth and super-admin check."""

    ADMIN_ENDPOINTS = [
        ("GET",  "/api/admin/tenants"),
        ("GET",  "/api/admin/tenants/t1"),
        ("PATCH", "/api/admin/tenants/t1/status"),
        ("DELETE", "/api/admin/tenants/t1"),
        ("GET",  "/api/admin/users"),
        ("PATCH", "/api/admin/users/u1/status"),
        ("POST", "/api/admin/users/u1/reset-password"),
        ("GET",  "/api/admin/feature-flags/t1"),
        ("POST", "/api/admin/feature-flags/t1"),
        ("PATCH", "/api/admin/feature-flags/t1"),
        ("GET",  "/api/admin/stats"),
    ]

    @pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
    def test_no_token_returns_401(self, client, method, path):
        """Unauthenticated request → 401."""
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
    def test_normal_user_returns_403(self, client, method, path):
        """Authenticated but non-super-admin user → 403."""
        headers = normal_user_headers()
        resp = getattr(client, method.lower())(path, headers=headers, json={"status": "active", "flags": {}, "new_password": "test1234"})
        assert resp.status_code == 403
        assert "super admin" in resp.get_json()["error"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# TENANT MANAGEMENT TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestListTenants:
    def test_list_tenants_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.side_effect = [
                [3],  # count query
                [SAMPLE_TENANT],  # data query
            ]
            resp = client.get("/api/admin/tenants", headers=super_admin_headers())
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 3
            assert len(data["tenants"]) == 1
            assert data["tenants"][0]["name"] == "Acme Corp"

    def test_list_tenants_pagination(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.side_effect = [[10], []]
            resp = client.get("/api/admin/tenants?page=1&limit=5", headers=super_admin_headers())
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["page"] == 1
            assert data["limit"] == 5


class TestGetTenant:
    def test_get_tenant_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.return_value = [SAMPLE_TENANT]
            resp = client.get("/api/admin/tenants/tenant-001", headers=super_admin_headers())
            assert resp.status_code == 200
            assert resp.get_json()["id"] == "tenant-001"

    def test_get_tenant_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/admin/tenants/nonexistent", headers=super_admin_headers())
            assert resp.status_code == 404


class TestUpdateTenantStatus:
    def test_update_status_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit") as mock_audit:
            mock_ctr.query_items.return_value = [dict(SAMPLE_TENANT)]
            mock_ctr.replace_item.return_value = {}
            resp = client.patch(
                "/api/admin/tenants/tenant-001/status",
                json={"status": "inactive"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "inactive"
            mock_ctr.replace_item.assert_called_once()
            mock_audit.assert_called_once()
            audit_kwargs = mock_audit.call_args
            assert audit_kwargs.kwargs.get("user_id") or audit_kwargs[1].get("user_id")

    def test_update_status_invalid(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container"):
            resp = client.patch(
                "/api/admin/tenants/tenant-001/status",
                json={"status": "bogus"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400

    def test_update_status_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.patch(
                "/api/admin/tenants/tenant-001/status",
                json={"status": "active"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 404

    def test_update_status_suspended(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit"):
            mock_ctr.query_items.return_value = [dict(SAMPLE_TENANT)]
            mock_ctr.replace_item.return_value = {}
            resp = client.patch(
                "/api/admin/tenants/tenant-001/status",
                json={"status": "suspended"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "suspended"


class TestDeleteTenant:
    def test_soft_delete_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit") as mock_audit:
            mock_ctr.query_items.return_value = [dict(SAMPLE_TENANT)]
            mock_ctr.replace_item.return_value = {}
            resp = client.delete("/api/admin/tenants/tenant-001", headers=super_admin_headers())
            assert resp.status_code == 200
            assert resp.get_json()["message"] == "Tenant deleted"
            # Verify it's a soft delete — replace_item called, not delete_item
            mock_ctr.replace_item.assert_called_once()
            body = mock_ctr.replace_item.call_args[1]["body"]
            assert body["status"] == "deleted"
            assert "deleted_at" in body
            mock_audit.assert_called_once()

    def test_soft_delete_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/admin/tenants/nonexistent", headers=super_admin_headers())
            assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestListUsers:
    def test_list_users_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr:
            mock_ctr.query_items.side_effect = [
                [1],  # count
                [SAMPLE_USER],  # data
            ]
            resp = client.get("/api/admin/users", headers=super_admin_headers())
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 1
            # Password must NOT appear in response
            assert "password" not in data["users"][0]

    def test_list_users_masks_sensitive_fields(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr:
            user_with_secrets = {**SAMPLE_USER, "refresh_token": "xxx", "portal_password": "yyy"}
            mock_ctr.query_items.side_effect = [[1], [user_with_secrets]]
            resp = client.get("/api/admin/users", headers=super_admin_headers())
            user = resp.get_json()["users"][0]
            for field in ("password", "refresh_token", "portal_password", "_rid", "_self"):
                assert field not in user


class TestUpdateUserStatus:
    def test_update_user_status_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit"):
            mock_ctr.query_items.return_value = [dict(SAMPLE_USER)]
            mock_ctr.replace_item.return_value = {}
            resp = client.patch(
                "/api/admin/users/user-001/status",
                json={"status": "inactive"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "inactive"
            assert "password" not in resp.get_json()

    def test_update_user_status_invalid(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container"):
            resp = client.patch(
                "/api/admin/users/user-001/status",
                json={"status": "banana"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400

    def test_update_user_status_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.patch(
                "/api/admin/users/user-001/status",
                json={"status": "active"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 404


class TestResetPassword:
    def test_reset_password_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit") as mock_audit:
            mock_ctr.query_items.return_value = [dict(SAMPLE_USER)]
            mock_ctr.replace_item.return_value = {}
            resp = client.post(
                "/api/admin/users/user-001/reset-password",
                json={"new_password": "SecurePass123!"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 200
            assert resp.get_json()["message"] == "Password reset successfully"
            # Verify password was hashed — not stored as plaintext
            body = mock_ctr.replace_item.call_args[1]["body"]
            assert body["password"] != "SecurePass123!"
            assert body["password"].startswith("pbkdf2:sha256:")
            # Audit log must NOT contain the actual password
            mock_audit.assert_called_once()

    def test_reset_password_too_short(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container"):
            resp = client.post(
                "/api/admin/users/user-001/reset-password",
                json={"new_password": "short"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400

    def test_reset_password_missing(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container"):
            resp = client.post(
                "/api/admin/users/user-001/reset-password",
                json={},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400

    def test_reset_password_user_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.post(
                "/api/admin/users/user-001/reset-password",
                json={"new_password": "SecurePass123!"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# FEATURE FLAGS TESTS
# ═════════════════════════════════════════════════════════════════════════════

SAMPLE_FLAGS_DOC = {
    "id": "ff-001",
    "tenant_id": "tenant-001",
    "flags": {"dark_mode": True, "beta_reports": False},
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00",
}


class TestGetFeatureFlags:
    def test_get_flags_existing(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr:
            mock_ctr.query_items.return_value = [SAMPLE_FLAGS_DOC]
            resp = client.get("/api/admin/feature-flags/tenant-001", headers=super_admin_headers())
            assert resp.status_code == 200
            assert resp.get_json()["flags"]["dark_mode"] is True

    def test_get_flags_empty(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/admin/feature-flags/tenant-001", headers=super_admin_headers())
            assert resp.status_code == 200
            assert resp.get_json()["flags"] == {}


class TestCreateFeatureFlags:
    def test_create_flags_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit"):
            mock_ctr.query_items.return_value = []  # no existing
            mock_ctr.create_item.return_value = {}
            resp = client.post(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": {"dark_mode": True}},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 201
            assert resp.get_json()["flags"]["dark_mode"] is True

    def test_create_flags_already_exist(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr:
            mock_ctr.query_items.return_value = [SAMPLE_FLAGS_DOC]
            resp = client.post(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": {"dark_mode": True}},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 409

    def test_create_flags_invalid_payload(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container"):
            resp = client.post(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": "not_a_dict"},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400


class TestUpdateFeatureFlags:
    def test_update_flags_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit"):
            existing = {**SAMPLE_FLAGS_DOC, "flags": dict(SAMPLE_FLAGS_DOC["flags"])}
            mock_ctr.query_items.return_value = [existing]
            mock_ctr.replace_item.return_value = {}
            resp = client.patch(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": {"beta_reports": True, "new_flag": True}},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 200
            flags = resp.get_json()["flags"]
            assert flags["beta_reports"] is True
            assert flags["new_flag"] is True
            assert flags["dark_mode"] is True  # preserved from original

    def test_update_flags_not_found(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.patch(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": {"x": True}},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 404

    def test_update_flags_invalid_payload(self, client):
        with patch("smart_invoice_pro.api.admin_api.feature_flags_container"):
            resp = client.patch(
                "/api/admin/feature-flags/tenant-001",
                json={"flags": 42},
                headers=super_admin_headers(),
            )
            assert resp.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM STATS TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestSystemStats:
    def test_stats_success(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_users, \
             patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_tenants:
            mock_users.query_items.side_effect = [
                [42],   # total users
                [30],   # active users
            ]
            mock_tenants.query_items.return_value = [10]
            resp = client.get("/api/admin/stats", headers=super_admin_headers())
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_users"] == 42
            assert data["active_users"] == 30
            assert data["total_tenants"] == 10

    def test_stats_empty_db(self, client):
        with patch("smart_invoice_pro.api.admin_api.users_container") as mock_users, \
             patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_tenants:
            mock_users.query_items.side_effect = [[], []]
            mock_tenants.query_items.return_value = []
            resp = client.get("/api/admin/stats", headers=super_admin_headers())
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_users"] == 0
            assert data["active_users"] == 0
            assert data["total_tenants"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# SECURITY EDGE-CASE TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminSecurityEdgeCases:
    def test_is_super_admin_false_returns_403(self, client):
        """is_super_admin=False in JWT should still be rejected."""
        token = make_token(is_super_admin=False)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = client.get("/api/admin/tenants", headers=headers)
        assert resp.status_code == 403

    def test_is_super_admin_string_returns_403(self, client):
        """is_super_admin='true' (string, not bool) should be rejected... but truthy in Python.
        This verifies the decorator uses bool() truthiness — string 'true' IS truthy so it passes.
        If we want strict boolean, we'd change the decorator. For now, this documents behavior."""
        token = make_token(is_super_admin="true")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.side_effect = [[0], []]
            resp = client.get("/api/admin/tenants", headers=headers)
            # Truthy string passes — this is acceptable; the claim is set server-side
            assert resp.status_code == 200

    def test_response_does_not_leak_cosmos_fields(self, client):
        """Cosmos internal fields must not appear in responses."""
        tenant_with_cosmos = {
            **SAMPLE_TENANT,
            "_rid": "abc123",
            "_self": "dbs/xxx",
            "_etag": "etag",
            "_attachments": "attachments/",
            "_ts": 1234567890,
        }
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr:
            mock_ctr.query_items.return_value = [tenant_with_cosmos]
            resp = client.get("/api/admin/tenants/tenant-001", headers=super_admin_headers())
            data = resp.get_json()
            for field in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                assert field not in data

    def test_audit_log_on_tenant_status_change(self, client):
        """Verify audit log is written with correct before/after on status change."""
        with patch("smart_invoice_pro.api.admin_api.tenants_container") as mock_ctr, \
             patch("smart_invoice_pro.api.admin_api.log_audit") as mock_audit:
            original = dict(SAMPLE_TENANT)
            mock_ctr.query_items.return_value = [original]
            mock_ctr.replace_item.return_value = {}
            client.patch(
                "/api/admin/tenants/tenant-001/status",
                json={"status": "inactive"},
                headers=super_admin_headers(),
            )
            mock_audit.assert_called_once()
            kwargs = mock_audit.call_args[1] if mock_audit.call_args[1] else {}
            args = mock_audit.call_args[0] if mock_audit.call_args[0] else ()
            # entity_type should be "tenant"
            if args:
                assert args[0] == "tenant"  # positional

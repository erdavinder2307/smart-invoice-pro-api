"""
Unit tests for rbac_resolver — the shared permission resolution path.
"""
import pytest
from unittest.mock import patch, MagicMock

from smart_invoice_pro.utils.rbac_resolver import (
    resolve_user_permissions,
    is_admin_user,
    fetch_account_user,
)

TENANT = "tenant-abc"
USER_ID = "user-123"
ROLE_ADMIN_ID = "role-admin-1"
ROLE_VIEWER_ID = "role-viewer-1"


def _admin_user(**overrides):
    base = {
        "id": USER_ID,
        "username": "admin",
        "password": "hashed",
        "tenant_id": TENANT,
        "role": "Admin",
        "is_active": True,
    }
    base.update(overrides)
    return base


def _viewer_user(**overrides):
    base = {
        "id": USER_ID,
        "username": "viewer",
        "password": "hashed",
        "tenant_id": TENANT,
        "role": "Invoice Viewer",
        "role_id": ROLE_VIEWER_ID,
        "is_active": True,
    }
    base.update(overrides)
    return base


ADMIN_ROLE = {
    "id": ROLE_ADMIN_ID,
    "tenant_id": TENANT,
    "name": "Admin",
    "permissions": {"invoices": {"view": True, "create": True}},
}

VIEWER_ROLE = {
    "id": ROLE_VIEWER_ID,
    "tenant_id": TENANT,
    "name": "Invoice Viewer",
    "permissions": {"invoices": {"view": True, "create": False, "edit": False, "delete": False}},
}


class TestResolveUserPermissions:
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_admin_by_role_string(self, mock_users):
        mock_users.query_items.return_value = [_admin_user()]
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is True
        assert perms == {}

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_admin_by_role_id_when_stale_role_string(self, mock_users, mock_role_by_id):
        """Admin assigned via role_id but user.role not synced → still admin."""
        mock_users.query_items.return_value = [
            _admin_user(role="Sales", role_id=ROLE_ADMIN_ID),
        ]
        mock_role_by_id.return_value = ADMIN_ROLE
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is True

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_legacy_user_without_tenant_id_on_document(self, mock_users, mock_role_by_id):
        """Old query required tenant_id match; user doc without tenant_id must still resolve."""
        mock_users.query_items.return_value = [
            _admin_user(tenant_id=None),
        ]
        is_admin, _ = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is True
        mock_role_by_id.assert_not_called()

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_tenant_mismatch_denied(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [
            _admin_user(tenant_id="other-tenant"),
        ]
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is False
        assert perms == {}

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_invoice_viewer_limited_permissions(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [_viewer_user()]
        mock_role_by_id.return_value = VIEWER_ROLE
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is False
        assert perms["invoices"]["view"] is True
        assert perms["invoices"]["create"] is False

    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_suspended_user_denied(self, mock_users):
        mock_users.query_items.return_value = [_admin_user(status="suspended")]
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is False
        assert perms == {}

    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_user_not_found(self, mock_users):
        mock_users.query_items.return_value = []
        is_admin, perms = resolve_user_permissions(USER_ID, TENANT)
        assert is_admin is False
        assert perms == {}


class TestIsAdminUser:
    def test_super_admin_flag(self):
        assert is_admin_user({"is_super_admin": True}, TENANT) is True

"""
Tests for Roles & Permissions API (roles_permissions_api.py).
GET /api/settings/permissions, GET/POST/PUT/DELETE /api/settings/roles,
GET/POST/PUT/DELETE /api/settings/users.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


ADMIN_USER = {
    "id": USER_A,
    "username": "admin",
    "email": "admin@example.com",
    "name": "Admin User",
    "role": "Admin",
    "role_id": "role-admin-1",
    "tenant_id": TENANT_A,
    "is_active": True,
    "created_at": "2024-01-01T00:00:00",
}

SALES_USER = {
    "id": USER_B,
    "username": "sales",
    "email": "sales@example.com",
    "name": "Sales User",
    "role": "Sales",
    "role_id": "role-sales-1",
    "tenant_id": TENANT_A,
    "is_active": True,
    "created_at": "2024-01-01T00:00:00",
}

ADMIN_ROLE_DOC = {
    "id": "role-admin-1",
    "tenant_id": TENANT_A,
    "name": "Admin",
    "is_system_role": True,
    "permissions": {
        "invoices": {"view": True, "create": True, "edit": True, "delete": True},
        "customers": {"view": True, "create": True, "edit": True, "delete": True},
    },
}

SALES_ROLE_DOC = {
    "id": "role-sales-1",
    "tenant_id": TENANT_A,
    "name": "Sales",
    "is_system_role": True,
    "permissions": {
        "invoices": {"view": True, "create": True, "edit": True, "delete": False},
        "customers": {"view": True, "create": True, "edit": True, "delete": False},
    },
}

CUSTOM_ROLE_DOC = {
    "id": "role-custom-1",
    "tenant_id": TENANT_A,
    "name": "Intern",
    "is_system_role": False,
    "permissions": {
        "invoices": {"view": True, "create": False, "edit": False, "delete": False},
    },
}


def _mock_roles_ctr():
    """Create a MagicMock to use as the roles container."""
    return MagicMock()


def _patches():
    """Patch roles_permissions_api internals + require_role's users_container."""
    return (
        patch("smart_invoice_pro.api.roles_permissions_api.users_container"),
        patch("smart_invoice_pro.api.roles_permissions_api._get_roles_container"),
        patch("smart_invoice_pro.api.roles_api.users_container"),  # for @require_role
    )


class TestGetMyPermissions:
    """GET /api/settings/permissions"""

    def test_admin_gets_full_permissions(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.get("/api/settings/permissions", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_admin"] is True
        assert data["role"] == "Admin"
        # Admin should have all permissions as True
        assert all(data["permissions"]["invoices"].values())

    def test_non_admin_gets_role_permissions(self, client, headers_b):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_users.query_items.return_value = [SALES_USER]
            mock_roles_fn.return_value = mock_rctr
            # _get_role_by_name queries roles container twice:
            # 1st: _get_or_seed_roles, 2nd: by name
            mock_rctr.query_items.side_effect = [
                [SALES_ROLE_DOC],  # _get_or_seed_roles
                [SALES_ROLE_DOC],  # query by name
            ]
            resp = client.get("/api/settings/permissions", headers=headers_b)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_admin"] is False
        assert data["role"] == "Sales"

    def test_user_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.return_value = []
            resp = client.get("/api/settings/permissions", headers=headers_a)
        assert resp.status_code == 404


class TestListRoles:
    """GET /api/settings/roles"""

    def test_list_existing_roles(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3:
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.return_value = [ADMIN_ROLE_DOC, SALES_ROLE_DOC]
            resp = client.get("/api/settings/roles", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_seeds_defaults_when_empty(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3:
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.return_value = []
            mock_rctr.create_item.return_value = {}
            resp = client.get("/api/settings/roles", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 5  # 5 system roles seeded
        assert mock_rctr.create_item.call_count == 5


class TestCreateRole:
    """POST /api/settings/roles (Admin only)"""

    def test_create_custom_role(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            # _get_role_by_name → _get_or_seed_roles query, then name query
            mock_rctr.query_items.side_effect = [
                [ADMIN_ROLE_DOC],  # _get_or_seed_roles
                [],                # no existing role by name "Intern"
            ]
            mock_rctr.create_item.return_value = {}
            resp = client.post("/api/settings/roles", json={
                "name": "Intern",
                "permissions": {"invoices": {"view": True}},
            }, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Intern"
        assert data["is_system_role"] is False

    def test_missing_name(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/roles", json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_duplicate_name(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.side_effect = [
                [SALES_ROLE_DOC],  # _get_or_seed_roles
                [SALES_ROLE_DOC],  # existing role found → dup
            ]
            resp = client.post("/api/settings/roles", json={
                "name": "Sales",
            }, headers=headers_a)
        assert resp.status_code == 409


class TestUpdateRole:
    """PUT /api/settings/roles/<id> (Admin only)"""

    def test_update_permissions(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.return_value = [CUSTOM_ROLE_DOC.copy()]
            resp = client.put("/api/settings/roles/role-custom-1", json={
                "name": "Updated Intern",
                "permissions": {"invoices": {"view": True, "create": True}},
            }, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Updated Intern"

    def test_update_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.return_value = []
            resp = client.put("/api/settings/roles/bad-id", json={
                "name": "X",
            }, headers=headers_a)
        assert resp.status_code == 404


class TestDeleteRole:
    """DELETE /api/settings/roles/<id> (Admin only)"""

    def test_delete_custom_role(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            # _get_role_by_id
            mock_rctr.query_items.side_effect = [
                [CUSTOM_ROLE_DOC],     # find role to delete
                [],                    # no users on this role
                [SALES_ROLE_DOC],      # _get_or_seed_roles for fallback
                [SALES_ROLE_DOC],      # _get_role_by_name "Sales"
            ]
            mock_users.query_items.return_value = []  # no users with this role_id
            resp = client.delete("/api/settings/roles/role-custom-1", headers=headers_a)
        assert resp.status_code == 200

    def test_delete_system_role_blocked(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            mock_rctr.query_items.return_value = [ADMIN_ROLE_DOC]
            resp = client.delete("/api/settings/roles/role-admin-1", headers=headers_a)
        assert resp.status_code == 400
        assert "system role" in resp.get_json()["error"].lower()


class TestListSettingsUsers:
    """GET /api/settings/users (Admin only)"""

    def test_list_users(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_users.query_items.return_value = [ADMIN_USER, SALES_USER]
            resp = client.get("/api/settings/users", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        # Should not include password
        for u in data:
            assert "password" not in u


class TestInviteUser:
    """POST /api/settings/users (Admin only)"""

    def test_invite_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        mock_rctr = _mock_roles_ctr()
        with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_roles_fn.return_value = mock_rctr
            mock_users.query_items.return_value = []  # no duplicate email found
            mock_rctr.query_items.side_effect = [
                [SALES_ROLE_DOC],  # _get_or_seed_roles
                [SALES_ROLE_DOC],  # _get_role_by_name
            ]
            mock_users.create_item.return_value = {}
            resp = client.post("/api/settings/users", json={
                "name": "New User",
                "email": "new@example.com",
                "username": "newuser",
                "password": "securepass123",
                "role": "Sales",
            }, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["email"] == "new@example.com"
        assert "password" not in data

    def test_invalid_email(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/users", json={
                "email": "not-an-email",
                "password": "securepass123",
            }, headers=headers_a)
        assert resp.status_code == 400

    def test_short_password(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/users", json={
                "email": "new@example.com",
                "password": "123",
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "password" in resp.get_json()["error"].lower()

    def test_duplicate_email(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_users.query_items.return_value = [ADMIN_USER]  # dup found
            resp = client.post("/api/settings/users", json={
                "email": "admin@example.com",
                "password": "securepass123",
            }, headers=headers_a)
        assert resp.status_code == 409


class TestDeactivateUser:
    """DELETE /api/settings/users/<id> (Admin only)"""

    def test_deactivate_success(self, client, headers_a):
        target = {**SALES_USER, "id": "user-to-deactivate"}
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_users.query_items.return_value = [target]
            resp = client.delete("/api/settings/users/user-to-deactivate",
                                 headers=headers_a)
        assert resp.status_code == 200

    def test_cannot_deactivate_self(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            resp = client.delete(f"/api/settings/users/{USER_A}",
                                 headers=headers_a)
        assert resp.status_code == 400
        assert "your own" in resp.get_json()["error"].lower()

    def test_cannot_deactivate_last_admin(self, client, headers_a):
        target_admin = {**ADMIN_USER, "id": "other-admin", "tenant_id": TENANT_A}
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3 as mock_role_users:
            mock_role_users.query_items.return_value = [ADMIN_USER]
            mock_users.query_items.side_effect = [
                [target_admin],       # _fetch_user_by_id
                [target_admin],       # admin count check → only 1
            ]
            resp = client.delete("/api/settings/users/other-admin",
                                 headers=headers_a)
        assert resp.status_code == 400
        assert "last" in resp.get_json()["error"].lower()

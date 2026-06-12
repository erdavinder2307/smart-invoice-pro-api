"""
Integration tests for require_permission — uses real rbac_resolver (not conftest admin mock).
"""
from unittest.mock import patch

from flask import Flask, request

from smart_invoice_pro.utils.permission_checker import require_permission

TENANT = "tenant-abc"
USER_ID = "user-123"


def _run_decorated(module, action):
    """Invoke a permission-guarded handler inside a Flask request context."""
    app = Flask(__name__)

    @require_permission(module, action)
    def handler():
        return {'ok': True}, 200

    with app.test_request_context('/test'):
        request.user_id = USER_ID
        request.tenant_id = TENANT
        return handler()


class TestRequirePermissionIntegration:
    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_admin_passes_invoices_view(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [{
            "id": USER_ID,
            "username": "admin",
            "password": "x",
            "tenant_id": TENANT,
            "role": "Admin",
        }]
        result = _run_decorated('invoices', 'view')
        assert result == ({'ok': True}, 200)

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_admin_via_role_id_with_stale_role_string(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [{
            "id": USER_ID,
            "username": "admin",
            "password": "x",
            "tenant_id": TENANT,
            "role": "Sales",
            "role_id": "role-admin-1",
        }]
        mock_role_by_id.return_value = {
            "id": "role-admin-1",
            "name": "Admin",
            "permissions": {"invoices": {"view": True}},
        }
        result = _run_decorated('invoices', 'view')
        assert result == ({'ok': True}, 200)

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_invoice_viewer_passes_invoices_view(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [{
            "id": USER_ID,
            "username": "viewer",
            "password": "x",
            "tenant_id": TENANT,
            "role": "Viewer",
            "role_id": "role-v1",
        }]
        mock_role_by_id.return_value = {
            "id": "role-v1",
            "name": "Viewer",
            "permissions": {"invoices": {"view": True, "create": False}},
        }
        result = _run_decorated('invoices', 'view')
        assert result == ({'ok': True}, 200)

    @patch("smart_invoice_pro.utils.rbac_resolver._get_role_by_id")
    @patch("smart_invoice_pro.utils.rbac_resolver.users_container")
    def test_invoice_viewer_denied_customers_view(self, mock_users, mock_role_by_id):
        mock_users.query_items.return_value = [{
            "id": USER_ID,
            "username": "viewer",
            "password": "x",
            "tenant_id": TENANT,
            "role": "Viewer",
            "role_id": "role-v1",
        }]
        mock_role_by_id.return_value = {
            "id": "role-v1",
            "name": "Viewer",
            "permissions": {"invoices": {"view": True}},
        }
        resp, status = _run_decorated('customers', 'view')
        assert status == 403
        body = resp.get_json()
        assert body['module'] == 'customers'
        assert body['action'] == 'view'

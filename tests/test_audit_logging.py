from unittest.mock import patch

from tests.conftest import TENANT_A, USER_A, auth_headers


class TestAuditHelper:

    @patch("smart_invoice_pro.utils.audit_logger._fire_and_forget_write")
    def test_log_audit_event_shapes_document(self, mock_async_write):
        from smart_invoice_pro.utils.audit_logger import log_audit_event

        log_audit_event(
            {
                "tenant_id": TENANT_A,
                "user_id": USER_A,
                "action": "update",
                "entity": "invoice",
                "entity_id": "inv-1",
                "before": {"status": "draft", "password": "secret"},
                "after": {"status": "issued", "token": "abc"},
                "metadata": {"portal_password": "hidden", "note": "ok"},
            }
        )

        assert mock_async_write.called
        doc = mock_async_write.call_args[0][0]
        assert doc["tenant_id"] == TENANT_A
        assert doc["user_id"] == USER_A
        assert doc["action"] == "UPDATE"
        assert doc["entity"] == "invoice"
        assert doc["entity_id"] == "inv-1"
        assert doc["before"]["status"] == "draft"
        assert doc["after"]["status"] == "issued"
        assert "password" not in doc["before"]
        assert "token" not in doc["after"]
        assert "portal_password" not in doc["metadata"]


class TestAuditEndpoints:

    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    @patch("smart_invoice_pro.api.roles_api.users_container")
    def test_get_audit_logs_returns_tenant_scoped_data(self, mock_users, mock_ctr, client, headers_a):
        mock_users.query_items.return_value = [{"id": USER_A, "role": "Admin"}]
        mock_ctr.query_items.side_effect = [
            [1],
            [
                {
                    "id": "log-1",
                    "tenant_id": TENANT_A,
                    "action": "CREATE",
                    "entity": "invoice",
                    "entity_id": "inv-1",
                    "before": None,
                    "after": {"status": "draft"},
                    "created_at": "2026-01-01T10:00:00",
                }
            ],
        ]

        resp = client.get("/api/audit-logs?action=CREATE", headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert len(body["logs"]) == 1
        assert body["logs"][0]["entity"] == "invoice"

    @patch("smart_invoice_pro.api.admin_api.audit_logs_container")
    def test_admin_audit_logs_requires_super_admin(self, mock_ctr, client, headers_a):
        resp = client.get("/api/admin/audit-logs", headers=headers_a)
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.admin_api.audit_logs_container")
    def test_admin_audit_logs_success(self, mock_ctr, client):
        super_admin_headers = auth_headers(user_id="super-admin", tenant_id="root-tenant", is_super_admin=True)
        mock_ctr.query_items.side_effect = [
            [1],
            [
                {
                    "id": "log-2",
                    "tenant_id": "tenant-x",
                    "action": "DELETE",
                    "entity_type": "customer",
                    "entity_id": "cust-1",
                    "changes": {"before": {"name": "Old"}, "after": None},
                    "timestamp": "2026-01-02T10:00:00",
                }
            ],
        ]

        resp = client.get("/api/admin/audit-logs?action=DELETE", headers=super_admin_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert body["logs"][0]["entity"] == "customer"


class TestAuditIntegration:

    @patch("smart_invoice_pro.api.product_api.log_audit_event")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_product_create_emits_audit_event(self, mock_products, mock_log, client, headers_a):
        mock_products.query_items.return_value = []

        resp = client.post(
            "/api/products",
            json={"name": "Widget", "price": 100, "unit": "Nos"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "CREATE"
        assert payload["entity"] == "product"

    @patch("smart_invoice_pro.api.routes.log_audit_event")
    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_emits_audit_event(self, mock_users, mock_refresh, mock_log, client):
        from werkzeug.security import generate_password_hash

        mock_users.query_items.return_value = [
            {
                "id": "user-1",
                "tenant_id": TENANT_A,
                "username": "demo",
                "password": generate_password_hash("secret123", method="pbkdf2:sha256", salt_length=16),
            }
        ]

        resp = client.post("/api/auth/login", json={"username": "demo", "password": "secret123"})
        assert resp.status_code == 200
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "LOGIN"
        assert payload["entity"] == "auth"

"""
Tests for Customer API — CRUD, validation, tenant isolation.
"""
from unittest.mock import patch

import pytest

from tests.conftest import TENANT_A, TENANT_B, USER_A, auth_headers


class TestCreateCustomer:

    @staticmethod
    def _error_payload(resp):
        return resp.get_json()["error"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_success(self, mock_cust, client, headers_a, sample_customer):
        mock_cust.query_items.return_value = []
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["display_name"] == "Acme Corp"
        assert data["email"] == "acme@example.com"
        assert "id" in data
        mock_cust.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_missing_display_name(self, mock_cust, client, headers_a):
        resp = client.post(
            "/api/customers",
            json={"email": "a@b.com", "phone": "9876543210"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "display_name" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_email_optional_when_phone_present(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.post(
            "/api/customers",
            json={
                "display_name": "Test",
                "phone": "9876543210",
                "customer_type": "individual",
            },
            headers=headers_a,
        )
        assert resp.status_code == 201

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_phone_optional_when_email_present(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.post(
            "/api/customers",
            json={
                "display_name": "Test",
                "email": "a@b.com",
                "customer_type": "individual",
            },
            headers=headers_a,
        )
        assert resp.status_code == 201

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_requires_email_or_phone(self, mock_cust, client, headers_a):
        resp = client.post(
            "/api/customers",
            json={"display_name": "Test"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "phone" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_duplicate_company_name(self, mock_cust, client, headers_a, sample_customer):
        mock_cust.query_items.side_effect = [
            [],
            [{
                "id": "existing-1",
                "company_name": "Acme Corp Pvt Ltd",
                "display_name": "Other",
                "status": "ACTIVE",
            }],
        ]
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 409

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_invalid_email(self, mock_cust, client, headers_a):
        resp = client.post(
            "/api/customers",
            json={"display_name": "Test", "email": "not-an-email", "phone": "9876543210"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "email" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_invalid_gst(self, mock_cust, client, headers_a, sample_customer):
        sample_customer["gst_number"] = "INVALID-GST"
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "gst_number" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_valid_gst(self, mock_cust, client, headers_a, sample_customer):
        sample_customer["gst_number"] = "06BZAHM6385P6Z2"
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 201

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_invalid_pan(self, mock_cust, client, headers_a, sample_customer):
        sample_customer["pan"] = "INVALID"
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "pan" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_create_customer_invalid_mobile(self, mock_cust, client, headers_a, sample_customer):
        sample_customer["mobile"] = "12345"
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 400
        error = self._error_payload(resp)
        assert error["type"] == "validation_error"
        assert "mobile" in error["fields"]

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_response_excludes_password(self, mock_cust, client, headers_a, sample_customer):
        sample_customer["portal_enabled"] = True
        sample_customer["portal_password"] = "secret"
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "portal_password" not in data
        assert "password" not in data

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_response_excludes_cosmos_fields(self, mock_cust, client, headers_a, sample_customer):
        resp = client.post("/api/customers", json=sample_customer, headers=headers_a)
        data = resp.get_json()
        for field in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            assert field not in data


class TestListCustomers:

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_list_returns_tenant_data(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = [
            {"id": "c-1", "display_name": "C1", "tenant_id": TENANT_A},
            {"id": "c-2", "display_name": "C2", "tenant_id": TENANT_A},
        ]
        resp = client.get("/api/customers", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_list_empty(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.get("/api/customers", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json() == []

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_list_applies_created_at_filter(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = [
            {"id": "c-1", "display_name": "C1", "tenant_id": TENANT_A, "created_at": "2026-04-10T12:00:00"},
        ]

        resp = client.get(
            "/api/customers?created_from=2026-04-01T00:00:00&created_to=2026-04-30T23:59:59.999999",
            headers=headers_a,
        )

        assert resp.status_code == 200
        call_kwargs = mock_cust.query_items.call_args.kwargs
        assert "c.created_at >= @created_from" in call_kwargs["query"]
        assert "c.created_at <= @created_to" in call_kwargs["query"]
        assert call_kwargs["parameters"] == [
            {"name": "@tenant_id", "value": TENANT_A},
            {"name": "@created_from", "value": "2026-04-01T00:00:00"},
            {"name": "@created_to", "value": "2026-04-30T23:59:59.999999"},
        ]


class TestGetCustomer:

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_get_customer_success(self, mock_cust, client, headers_a, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.get("/api/customers/cust-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["display_name"] == "Acme Corp"

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_get_customer_not_found(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.get("/api/customers/nonexistent", headers=headers_a)
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_get_customer_cross_tenant(self, mock_cust, client, headers_b, stored_customer_a):
        """Tenant B cannot access Tenant A's customer."""
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.get("/api/customers/cust-aaa-001", headers=headers_b)
        assert resp.status_code == 403


class TestUpdateCustomer:

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_update_customer_success(self, mock_cust, client, headers_a, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.put(
            "/api/customers/cust-aaa-001",
            json={"display_name": "Acme Corp Updated"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["display_name"] == "Acme Corp Updated"

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_update_customer_invalid_email(self, mock_cust, client, headers_a, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.put(
            "/api/customers/cust-aaa-001",
            json={"email": "bad-email"},
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_update_customer_not_found(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.put(
            "/api/customers/nonexistent",
            json={"display_name": "X"},
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_update_customer_cross_tenant(self, mock_cust, client, headers_b, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.put(
            "/api/customers/cust-aaa-001",
            json={"display_name": "HACKED"},
            headers=headers_b,
        )
        assert resp.status_code == 403


class TestDeleteCustomer:

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_delete_customer_success(self, mock_cust, client, headers_a, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.delete("/api/customers/cust-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        # Lifecycle-aware delete: no dependencies → hard delete (delete_item) or archive (replace_item)
        assert mock_cust.delete_item.called or mock_cust.replace_item.called

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_delete_customer_not_found(self, mock_cust, client, headers_a):
        mock_cust.query_items.return_value = []
        resp = client.delete("/api/customers/nonexistent", headers=headers_a)
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_delete_customer_cross_tenant(self, mock_cust, client, headers_b, stored_customer_a):
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.delete("/api/customers/cust-aaa-001", headers=headers_b)
        assert resp.status_code == 403
        mock_cust.delete_item.assert_not_called()

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_deleted_customer_not_accessible(self, mock_cust, client, headers_a, stored_customer_a):
        """After deletion the item should not be returned."""
        # First call for delete
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.delete("/api/customers/cust-aaa-001", headers=headers_a)
        assert resp.status_code == 200

        # Second call for get
        mock_cust.query_items.return_value = []
        resp = client.get("/api/customers/cust-aaa-001", headers=headers_a)
        assert resp.status_code == 404


class TestMergeCustomer:
    """POST /customers/<source_id>/merge-into/<target_id> tests."""

    _TARGET = {
        "id": "cust-aaa-002",
        "display_name": "Acme Corp Primary",
        "email": "primary@example.com",
        "tenant_id": "tenant-aaa-1111",
        "created_at": "2025-06-01T00:00:00",
        "updated_at": "2025-06-01T00:00:00",
    }

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_merge_same_id_rejected(self, mock_cust, client, headers_a):
        """source == target returns 400 without touching the DB."""
        resp = client.post(
            "/api/customers/cust-aaa-001/merge-into/cust-aaa-001",
            headers=headers_a,
        )
        assert resp.status_code == 400
        mock_cust.query_items.assert_not_called()

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_merge_source_not_found(self, mock_cust, client, headers_a):
        """Non-existent source → 404."""
        mock_cust.query_items.return_value = []
        resp = client.post(
            "/api/customers/nonexistent/merge-into/cust-aaa-002",
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_merge_cross_tenant_source(self, mock_cust, client, headers_b, stored_customer_a):
        """Tenant B cannot merge Tenant A's source customer → 403."""
        mock_cust.query_items.return_value = [stored_customer_a]
        resp = client.post(
            "/api/customers/cust-aaa-001/merge-into/cust-aaa-002",
            headers=headers_b,
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_merge_success(self, mock_cust, client, headers_a, stored_customer_a):
        """Happy path — reparents (0) entities and archives source → 200."""
        target = dict(self._TARGET)

        def _query_effect(**kwargs):
            params = kwargs.get("parameters", [])
            id_value = next((p["value"] for p in params if p.get("name") == "@id"), None)
            if id_value == "cust-aaa-001":
                return [stored_customer_a]
            if id_value == "cust-aaa-002":
                return [target]
            return []

        mock_cust.query_items.side_effect = _query_effect

        resp = client.post(
            "/api/customers/cust-aaa-001/merge-into/cust-aaa-002",
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["source_id"] == "cust-aaa-001"
        assert data["target_id"] == "cust-aaa-002"
        assert data["invoices_reparented"] == 0
        assert data["quotes_reparented"] == 0
        # Source customer must have been archived (replace_item) or hard-deleted (delete_item)
        assert mock_cust.replace_item.called or mock_cust.delete_item.called

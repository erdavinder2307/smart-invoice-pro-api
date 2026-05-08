"""Tests for sales orders API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


SAMPLE_SO = {
    "so_number": "SO-001",
    "customer_id": "cust-001",
    "customer_name": "Acme Corp",
    "order_date": "2026-03-01",
    "total_amount": 8000.0,
    "status": "Draft",
    "items": [],
}

STORED_SO_A = {
    "id": "so-001",
    "so_number": "SO-001",
    "customer_id": "cust-001",
    "customer_name": "Acme Corp",
    "order_date": "2026-03-01",
    "total_amount": 8000.0,
    "status": "Draft",
    "tenant_id": TENANT_A,
    "items": [],
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreateSalesOrder:
    """POST /api/sales-orders tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.create_item.return_value = {**SAMPLE_SO, "id": "new-id", "tenant_id": TENANT_A}
            resp = client.post("/api/sales-orders", json=SAMPLE_SO, headers=headers_a)
            assert resp.status_code == 201

    def test_create_stores_tenant_id(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            client.post("/api/sales-orders", json=SAMPLE_SO, headers=headers_a)
            call_args = mock_ctr.create_item.call_args
            body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
            assert body["tenant_id"] == TENANT_A

    def test_create_missing_required_fields(self, client, headers_a):
        resp = client.post("/api/sales-orders", json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_create_invalid_status(self, client, headers_a):
        payload = {**SAMPLE_SO, "status": "BadStatus"}
        resp = client.post("/api/sales-orders", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_delivery_before_order(self, client, headers_a):
        payload = {**SAMPLE_SO, "delivery_date": "2026-02-01"}
        resp = client.post("/api/sales-orders", json=payload, headers=headers_a)
        assert resp.status_code == 400


class TestListSalesOrders:
    """GET /api/sales-orders tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_SO_A]
            resp = client.get("/api/sales-orders", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/sales-orders", headers=headers_a)
            assert resp.status_code == 200


class TestGetSalesOrder:
    """GET /api/sales-orders/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_SO_A]
            resp = client.get("/api/sales-orders/so-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/sales-orders/nope", headers=headers_a)
            assert resp.status_code == 404

    def test_get_cross_tenant_not_visible(self, client, headers_b):
        """Sales orders filter by tenant_id in query — cross-tenant yields 404."""
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            # Mock returns empty because DB query filters by tenant_id
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/sales-orders/so-001", headers=headers_b)
            assert resp.status_code == 404


class TestUpdateSalesOrder:
    """PUT /api/sales-orders/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_SO_A]
            mock_ctr.replace_item.return_value = {**STORED_SO_A, "notes": "updated"}
            resp = client.put("/api/sales-orders/so-001", json={"notes": "updated"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/sales-orders/nope", json={"notes": "x"}, headers=headers_a)
            assert resp.status_code == 404

    def test_update_invalid_status(self, client, headers_a):
        resp = client.put("/api/sales-orders/so-001", json={"status": "BadStatus"}, headers=headers_a)
        assert resp.status_code == 400


class TestDeleteSalesOrder:
    """DELETE /api/sales-orders/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_SO_A]
            resp = client.delete("/api/sales-orders/so-001", headers=headers_a)
            assert resp.status_code == 200
            assert resp.get_json()["message"] == "Sales Order archived successfully"
            mock_ctr.replace_item.assert_called_once()

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/sales-orders/nope", headers=headers_a)
            assert resp.status_code == 404

    def test_delete_invoiced_so_blocked(self, client, headers_a):
        """Cannot archive an invoiced sales order."""
        invoiced = {**STORED_SO_A, "status": "Invoiced"}
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [invoiced]
            resp = client.delete("/api/sales-orders/so-001", headers=headers_a)
            assert resp.status_code == 400


class TestSONextNumber:
    """GET /api/sales-orders/next-number tests."""

    def test_next_number(self, client, headers_a):
        with patch("smart_invoice_pro.api.sales_orders_api.sales_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/sales-orders/next-number", headers=headers_a)
            assert resp.status_code == 200

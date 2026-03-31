"""Tests for purchase orders API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


SAMPLE_PO = {
    "po_number": "PO-001",
    "vendor_id": "vendor-001",
    "vendor_name": "Supplier Corp",
    "order_date": "2026-03-01",
    "total_amount": 5000.0,
    "status": "Draft",
    "items": [],
}

STORED_PO = {
    "id": "po-001",
    "po_number": "PO-001",
    "vendor_id": "vendor-001",
    "vendor_name": "Supplier Corp",
    "order_date": "2026-03-01",
    "total_amount": 5000.0,
    "status": "Draft",
    "items": [],
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreatePurchaseOrder:
    """POST /api/purchase-orders tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.create_item.return_value = {**SAMPLE_PO, "id": "new-id"}
            resp = client.post("/api/purchase-orders", json=SAMPLE_PO, headers=headers_a)
            assert resp.status_code == 201

    def test_create_missing_required_fields(self, client, headers_a):
        resp = client.post("/api/purchase-orders", json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_create_invalid_status(self, client, headers_a):
        payload = {**SAMPLE_PO, "status": "BadStatus"}
        resp = client.post("/api/purchase-orders", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_delivery_before_order(self, client, headers_a):
        payload = {**SAMPLE_PO, "delivery_date": "2026-02-01"}
        resp = client.post("/api/purchase-orders", json=payload, headers=headers_a)
        assert resp.status_code == 400


class TestListPurchaseOrders:
    """GET /api/purchase-orders tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PO]
            resp = client.get("/api/purchase-orders", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/purchase-orders", headers=headers_a)
            assert resp.status_code == 200


class TestGetPurchaseOrder:
    """GET /api/purchase-orders/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PO]
            resp = client.get("/api/purchase-orders/po-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/purchase-orders/nope", headers=headers_a)
            assert resp.status_code == 404


class TestUpdatePurchaseOrder:
    """PUT /api/purchase-orders/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PO]
            mock_ctr.replace_item.return_value = {**STORED_PO, "notes": "updated"}
            resp = client.put("/api/purchase-orders/po-001", json={"notes": "updated"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/purchase-orders/nope", json={"notes": "x"}, headers=headers_a)
            assert resp.status_code == 404

    def test_update_invalid_status(self, client, headers_a):
        resp = client.put("/api/purchase-orders/po-001", json={"status": "BadStatus"}, headers=headers_a)
        assert resp.status_code == 400


class TestDeletePurchaseOrder:
    """DELETE /api/purchase-orders/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PO]
            resp = client.delete("/api/purchase-orders/po-001", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/purchase-orders/nope", headers=headers_a)
            assert resp.status_code == 404

    def test_delete_billed_po_blocked(self, client, headers_a):
        """Cannot delete a billed PO."""
        billed = {**STORED_PO, "status": "Billed"}
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = [billed]
            resp = client.delete("/api/purchase-orders/po-001", headers=headers_a)
            assert resp.status_code == 400


class TestPONextNumber:
    """GET /api/purchase-orders/next-number tests."""

    def test_next_number(self, client, headers_a):
        with patch("smart_invoice_pro.api.purchase_orders_api.purchase_orders_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/purchase-orders/next-number", headers=headers_a)
            assert resp.status_code == 200

"""Tests for bills API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, TENANT_B, USER_A


SAMPLE_BILL = {
    "bill_number": "BILL-001",
    "vendor_id": "vendor-001",
    "vendor_name": "Supplier Corp",
    "bill_date": "2026-03-01",
    "due_date": "2026-03-31",
    "total_amount": 2000.0,
    "items": [],
}

STORED_BILL_A = {
    "id": "bill-aaa-001",
    "bill_number": "BILL-001",
    "vendor_id": "vendor-001",
    "vendor_name": "Supplier Corp",
    "bill_date": "2026-03-01",
    "due_date": "2026-03-31",
    "total_amount": 2000.0,
    "amount_paid": 0.0,
    "balance_due": 2000.0,
    "payment_status": "Unpaid",
    "payment_history": [],
    "items": [],
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreateBill:
    """POST /api/bills tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr, \
             patch("smart_invoice_pro.api.bills_api.get_container") as mock_gc:
            mock_ctr.create_item.return_value = {**SAMPLE_BILL, "id": "new-id"}
            resp = client.post("/api/bills", json=SAMPLE_BILL, headers=headers_a)
            assert resp.status_code == 201

    def test_create_missing_required_fields(self, client, headers_a):
        resp = client.post("/api/bills", json={}, headers=headers_a)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "details" in data

    def test_create_invalid_payment_status(self, client, headers_a):
        payload = {**SAMPLE_BILL, "payment_status": "Invalid"}
        resp = client.post("/api/bills", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_due_before_bill_date(self, client, headers_a):
        payload = {**SAMPLE_BILL, "bill_date": "2026-04-01", "due_date": "2026-03-01"}
        resp = client.post("/api/bills", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_with_stock_items(self, client, headers_a):
        """Bill items with product_id create IN stock transactions."""
        mock_stock_ctr = MagicMock()
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr, \
             patch("smart_invoice_pro.api.bills_api.get_container", return_value=mock_stock_ctr):
            payload = {**SAMPLE_BILL, "items": [{"product_id": "p1", "quantity": 10}]}
            mock_ctr.create_item.return_value = {**payload, "id": "new-id"}
            resp = client.post("/api/bills", json=payload, headers=headers_a)
            assert resp.status_code == 201
            # Verify stock IN transaction was created
            stock_call = mock_stock_ctr.create_item.call_args
            body = stock_call[1]["body"] if "body" in stock_call[1] else stock_call[0][0]
            assert body["type"] == "IN"
            assert body["quantity"] == 10.0

    def test_create_defaults_balance_due(self, client, headers_a):
        """Balance due defaults to total_amount when not provided."""
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr, \
             patch("smart_invoice_pro.api.bills_api.get_container"):
            mock_ctr.create_item.return_value = {}
            client.post("/api/bills", json=SAMPLE_BILL, headers=headers_a)
            call_args = mock_ctr.create_item.call_args
            body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
            assert body["balance_due"] == 2000.0
            assert body["payment_status"] == "Unpaid"


class TestListBills:
    """GET /api/bills tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_BILL_A]
            resp = client.get("/api/bills", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/bills", headers=headers_a)
            assert resp.status_code == 200


class TestGetBill:
    """GET /api/bills/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_BILL_A]
            resp = client.get("/api/bills/bill-aaa-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/bills/nonexistent", headers=headers_a)
            assert resp.status_code == 404


class TestUpdateBill:
    """PUT /api/bills/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_BILL_A]
            mock_ctr.replace_item.return_value = {**STORED_BILL_A, "notes": "updated"}
            resp = client.put("/api/bills/bill-aaa-001", json={"notes": "updated"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/bills/nope", json={"notes": "x"}, headers=headers_a)
            assert resp.status_code == 404

    def test_update_invalid_status(self, client, headers_a):
        resp = client.put("/api/bills/bill-aaa-001", json={"payment_status": "BadStatus"}, headers=headers_a)
        assert resp.status_code == 400


class TestDeleteBill:
    """DELETE /api/bills/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_BILL_A]
            resp = client.delete("/api/bills/bill-aaa-001", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/bills/nope", headers=headers_a)
            assert resp.status_code == 404

    def test_delete_paid_bill_blocked(self, client, headers_a):
        """Cannot delete a paid bill."""
        paid_bill = {**STORED_BILL_A, "payment_status": "Paid"}
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [paid_bill]
            resp = client.delete("/api/bills/bill-aaa-001", headers=headers_a)
            assert resp.status_code == 400


class TestBillRecordPayment:
    """POST /api/bills/<id>/record-payment tests."""

    def test_record_payment_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_BILL_A.copy()]
            updated = {**STORED_BILL_A, "amount_paid": 500, "balance_due": 1500, "payment_status": "Partially Paid"}
            mock_ctr.replace_item.return_value = updated
            payload = {"amount": 500, "payment_date": "2026-03-15"}
            resp = client.post("/api/bills/bill-aaa-001/record-payment", json=payload, headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["payment_status"] == "Partially Paid"

    def test_record_payment_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            payload = {"amount": 500, "payment_date": "2026-03-15"}
            resp = client.post("/api/bills/nope/record-payment", json=payload, headers=headers_a)
            assert resp.status_code == 404


class TestBillNextNumber:
    """GET /api/bills/next-number tests."""

    def test_next_number(self, client, headers_a):
        with patch("smart_invoice_pro.api.bills_api.bills_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/bills/next-number", headers=headers_a)
            assert resp.status_code == 200

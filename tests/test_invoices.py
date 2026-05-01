"""
Tests for Invoice API — CRUD, calculations, status flow, tenant isolation.
"""
from unittest.mock import patch, MagicMock
import copy

import pytest

from tests.conftest import TENANT_A, TENANT_B, USER_A


class TestCreateInvoice:

    @staticmethod
    def _with_valid_item(payload):
        next_payload = copy.deepcopy(payload)
        next_payload["items"] = [
            {
                "name": "Implementation Service",
                "quantity": 2,
                "rate": 500,
                "discount": 0,
                "tax": 0,
            }
        ]
        return next_payload

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_success(self, mock_inv, mock_get_ctr, client, headers_a, sample_invoice):
        mock_get_ctr.return_value = MagicMock()  # stock_container
        resp = client.post("/api/invoices", json=self._with_valid_item(sample_invoice), headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data
        assert data["status"] == "Draft"
        assert data["tenant_id"] == TENANT_A
        mock_inv.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_stores_tenant_id(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        client.post("/api/invoices", json=self._with_valid_item(sample_invoice), headers=headers_a)
        created = mock_inv.create_item.call_args[1]["body"]
        assert created["tenant_id"] == TENANT_A

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_default_balance_due(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        resp = client.post("/api/invoices", json=self._with_valid_item(sample_invoice), headers=headers_a)
        data = resp.get_json()
        assert data["balance_due"] == data["total_amount"]

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_stock_decrement(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        """Invoice creation should create OUT stock transactions for each line item."""
        mock_stock = MagicMock()
        mock_gc.return_value = mock_stock
        sample_invoice["items"] = [
            {"product_id": "p-1", "product_name": "Widget", "quantity": 5, "rate": 100, "amount": 500},
        ]
        resp = client.post("/api/invoices", json=sample_invoice, headers=headers_a)
        assert resp.status_code == 201
        mock_stock.create_item.assert_called_once()
        stock_txn = mock_stock.create_item.call_args[1]["body"]
        assert stock_txn["type"] == "OUT"
        assert stock_txn["quantity"] == 5.0

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_generates_portal_token(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        resp = client.post("/api/invoices", json=self._with_valid_item(sample_invoice), headers=headers_a)
        data = resp.get_json()
        assert "portal_token" in data
        assert len(data["portal_token"]) > 20

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_rejects_due_date_before_issue_date(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        payload = self._with_valid_item(sample_invoice)
        payload["issue_date"] = "2026-04-20"
        payload["due_date"] = "2026-04-10"

        resp = client.post("/api/invoices", json=payload, headers=headers_a)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "Validation failed"
        assert body["details"]["due_date"] == "Due date must be on or after invoice date."
        mock_inv.create_item.assert_not_called()

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_invoice_rejects_invalid_item_quantity(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        payload = self._with_valid_item(sample_invoice)
        payload["items"][0]["quantity"] = 0

        resp = client.post("/api/invoices", json=payload, headers=headers_a)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "Validation failed"
        assert body["details"]["items[0].quantity"] == "Quantity must be greater than 0."
        mock_inv.create_item.assert_not_called()


class TestListInvoices:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_list_invoices(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = [
            {"id": "i-1", "invoice_number": "INV-001", "tenant_id": TENANT_A},
        ]
        resp = client.get("/api/invoices", headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_list_invoices_empty(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.get("/api/invoices", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestGetInvoice:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_get_invoice_success(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.get("/api/invoices/inv-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["invoice_number"] == "INV-001"

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_get_invoice_not_found(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.get("/api/invoices/nonexistent", headers=headers_a)
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_get_invoice_cross_tenant(self, mock_inv, client, headers_b, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.get("/api/invoices/inv-aaa-001", headers=headers_b)
        assert resp.status_code == 403


class TestUpdateInvoice:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_update_invoice_success(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.put(
            "/api/invoices/inv-aaa-001",
            json={"status": "Issued", "invoice_number": "INV-001", "customer_id": "cust-001",
                  "issue_date": "2025-06-01", "due_date": "2025-06-15",
                  "subtotal": 1000, "total_amount": 1180,
                  "items": [{"name": "Implementation Service", "quantity": 2, "rate": 500, "discount": 0, "tax": 0}]},
            headers=headers_a,
        )
        assert resp.status_code == 200

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_update_invoice_cross_tenant(self, mock_inv, client, headers_b, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.put(
            "/api/invoices/inv-aaa-001",
            json={"status": "Cancelled"},
            headers=headers_b,
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_update_invoice_not_found(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.put(
            "/api/invoices/nonexistent",
            json={"status": "Issued"},
            headers=headers_a,
        )
        assert resp.status_code == 404


class TestPatchInvoice:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_valid_status(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.patch(
            "/api/invoices/inv-aaa-001",
            json={"status": "Paid"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["invoice"]["status"] == "Paid"

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_invalid_status(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.patch(
            "/api/invoices/inv-aaa-001",
            json={"status": "InvalidStatus"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "details" in resp.get_json()

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_unknown_field(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.patch(
            "/api/invoices/inv-aaa-001",
            json={"foo_bar": "test"},
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_empty_body(self, mock_inv, client, headers_a):
        resp = client.patch("/api/invoices/inv-aaa-001", json={}, headers=headers_a)
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_cross_tenant(self, mock_inv, client, headers_b, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.patch(
            "/api/invoices/inv-aaa-001",
            json={"status": "Cancelled"},
            headers=headers_b,
        )
        assert resp.status_code == 403


class TestDeleteInvoice:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_delete_invoice_success(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.delete("/api/invoices/inv-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        mock_inv.delete_item.assert_called_once()

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_delete_invoice_not_found(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.delete("/api/invoices/nonexistent", headers=headers_a)
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_delete_invoice_cross_tenant(self, mock_inv, client, headers_b, stored_invoice_a):
        mock_inv.query_items.return_value = [stored_invoice_a]
        resp = client.delete("/api/invoices/inv-aaa-001", headers=headers_b)
        assert resp.status_code == 403
        mock_inv.delete_item.assert_not_called()


class TestNextInvoiceNumber:

    @patch("smart_invoice_pro.api.invoices.peek_next_invoice_number")
    def test_next_number(self, mock_peek, client, headers_a):
        mock_peek.return_value = "INV-007"
        resp = client.get("/api/invoices/next-number", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["next_invoice_number"] == "INV-007"


class TestResponseSanitization:

    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_no_cosmos_internal_fields(self, mock_inv, mock_gc, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        resp = client.post("/api/invoices", json=sample_invoice, headers=headers_a)
        data = resp.get_json()
        for key in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            assert key not in data

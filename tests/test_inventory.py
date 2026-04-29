"""
Tests for Stock / Inventory API — add, reduce, adjust, ledger, current stock.
"""
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import TENANT_A


class TestStockAdd:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    @patch("smart_invoice_pro.api.stock_api.products_container")
    def test_add_stock_success(self, mock_products, mock_stock, client, headers_a):
        mock_products.query_items.return_value = [{"id": "p-1", "tenant_id": TENANT_A, "is_deleted": False}]
        mock_stock.query_items.return_value = [{"type": "IN", "quantity": 50}]
        resp = client.post(
            "/api/stock/add",
            json={"product_id": "p-1", "quantity": 50, "source": "Purchase"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "Stock added"
        txn = data["transaction"]
        assert txn["type"] == "IN"
        assert txn["quantity"] == 50.0
        assert txn["tenant_id"] == TENANT_A
        assert data["current_stock"] == 50.0
        assert data["operation"] == "increase"
        mock_stock.create_item.assert_called_once()


class TestStockReduce:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    @patch("smart_invoice_pro.api.stock_api.products_container")
    def test_reduce_stock_success(self, mock_products, mock_stock, client, headers_a):
        mock_products.query_items.return_value = [{"id": "p-1", "tenant_id": TENANT_A, "is_deleted": False}]
        mock_stock.query_items.return_value = [{"type": "OUT", "quantity": 10}]
        resp = client.post(
            "/api/stock/reduce",
            json={"product_id": "p-1", "quantity": 10, "source": "Sale"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "Stock reduced"
        txn = data["transaction"]
        assert txn["type"] == "OUT"
        assert txn["quantity"] == 10.0
        assert txn["tenant_id"] == TENANT_A
        assert data["current_stock"] == -10.0
        assert data["operation"] == "decrease"


class TestCurrentStock:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_current_stock_calculation(self, mock_stock, client, headers_a):
        mock_stock.query_items.return_value = [
            {"type": "IN", "quantity": 100},
            {"type": "IN", "quantity": 50},
            {"type": "OUT", "quantity": 30},
        ]
        resp = client.get("/api/stock/p-1", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["current_stock"] == 120  # 100+50-30
        assert data["stock_in"] == 150.0
        assert data["stock_out"] == 30.0

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_current_stock_can_be_negative(self, mock_stock, client, headers_a):
        """Negative stock is allowed and should be reported accurately."""
        mock_stock.query_items.return_value = [
            {"type": "IN", "quantity": 10},
            {"type": "OUT", "quantity": 50},
        ]
        resp = client.get("/api/stock/p-1", headers=headers_a)
        data = resp.get_json()
        assert data["current_stock"] == -40.0

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_current_stock_no_transactions(self, mock_stock, client, headers_a):
        mock_stock.query_items.return_value = []
        resp = client.get("/api/stock/p-1", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["current_stock"] == 0


class TestStockLedger:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_ledger_running_balance(self, mock_stock, client, headers_a):
        mock_stock.query_items.return_value = [
            {"type": "IN", "quantity": 100, "timestamp": "2025-06-01T00:00:00"},
            {"type": "OUT", "quantity": 20, "timestamp": "2025-06-02T00:00:00"},
            {"type": "IN", "quantity": 10, "timestamp": "2025-06-03T00:00:00"},
        ]
        resp = client.get("/api/stock/ledger/p-1", headers=headers_a)
        assert resp.status_code == 200
        ledger = resp.get_json()
        assert len(ledger) == 3
        assert ledger[0]["balance"] == 100
        assert ledger[1]["balance"] == 80
        assert ledger[2]["balance"] == 90

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_ledger_empty(self, mock_stock, client, headers_a):
        mock_stock.query_items.return_value = []
        resp = client.get("/api/stock/ledger/p-1", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestStockAdjust:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_adjustment_success(self, mock_stock, client, headers_a):
        resp = client.post(
            "/api/stock/adjust",
            json={
                "product_id": "p-1",
                "type": "DAMAGE",
                "quantity": -5,
                "reason": "Warehouse damage",
            },
            headers=headers_a,
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "Stock adjustment processed successfully"
        mock_stock.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_adjustment_missing_fields(self, mock_stock, client, headers_a):
        resp = client.post(
            "/api/stock/adjust",
            json={"product_id": "p-1"},  # missing type, quantity, reason
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "missing required field" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_adjustment_zero_quantity(self, mock_stock, client, headers_a):
        resp = client.post(
            "/api/stock/adjust",
            json={
                "product_id": "p-1",
                "type": "RETURN",
                "quantity": 0,
                "reason": "Test",
            },
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "zero" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_adjustment_invalid_quantity(self, mock_stock, client, headers_a):
        resp = client.post(
            "/api/stock/adjust",
            json={
                "product_id": "p-1",
                "type": "RETURN",
                "quantity": "abc",
                "reason": "Test",
            },
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_adjustment_no_body(self, mock_stock, client, headers_a):
        resp = client.post(
            "/api/stock/adjust",
            data="",
            content_type="application/json",
            headers=headers_a,
        )
        # Empty body hits the broad except → 500 or the "No data provided" check → 400
        assert resp.status_code in (400, 500)


class TestRecentAdjustments:

    @patch("smart_invoice_pro.api.stock_api.stock_container")
    def test_recent_adjustments(self, mock_stock, client, headers_a):
        mock_stock.query_items.return_value = [
            {"id": "adj-1", "quantity": 10, "type": "IN", "timestamp": "2025-06-01T00:00:00"},
        ]
        resp = client.get("/api/stock/recent-adjustments", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1


class TestStockTest:

    def test_stock_test_endpoint(self, client, headers_a):
        resp = client.get("/api/stock/test", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Stock API is working!"

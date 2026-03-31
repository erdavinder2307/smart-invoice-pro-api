"""Tests for dashboard API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


class TestDashboardSummary:
    """GET /api/dashboard/summary tests."""

    def test_summary_returns_all_metrics(self, client, headers_a):
        """Happy path — returns all summary fields."""
        with patch("smart_invoice_pro.api.dashboard_api.customers_container") as mock_cust, \
             patch("smart_invoice_pro.api.dashboard_api.products_container") as mock_prod, \
             patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv, \
             patch("smart_invoice_pro.api.dashboard_api.bills_container") as mock_bills:
            mock_cust.read_all_items.return_value = [{"id": "c1"}, {"id": "c2"}]
            mock_prod.read_all_items.return_value = [{"id": "p1"}]
            mock_inv.read_all_items.return_value = [
                {"id": "i1", "total_amount": 1000, "balance_due": 500, "status": "Issued", "due_date": "2020-01-01"},
                {"id": "i2", "total_amount": 2000, "balance_due": 0, "status": "Paid"},
            ]
            mock_bills.read_all_items.return_value = [
                {"total_amount": 500, "balance_due": 500, "payment_status": "Unpaid"},
            ]

            resp = client.get("/api/dashboard/summary", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_customers"] == 2
            assert data["total_products"] == 1
            assert data["total_invoices"] == 2
            assert data["total_revenue"] == 3000.0
            assert data["total_receivables"] == 500.0
            assert data["total_payables"] == 500.0

    def test_summary_empty_data(self, client, headers_a):
        """Empty database returns zeroes."""
        with patch("smart_invoice_pro.api.dashboard_api.customers_container") as mock_cust, \
             patch("smart_invoice_pro.api.dashboard_api.products_container") as mock_prod, \
             patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv, \
             patch("smart_invoice_pro.api.dashboard_api.bills_container") as mock_bills:
            mock_cust.read_all_items.return_value = []
            mock_prod.read_all_items.return_value = []
            mock_inv.read_all_items.return_value = []
            mock_bills.read_all_items.return_value = []

            resp = client.get("/api/dashboard/summary", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_customers"] == 0
            assert data["total_revenue"] == 0


class TestDashboardLowStock:
    """GET /api/dashboard/low-stock tests."""

    def test_low_stock_default_threshold(self, client, headers_a):
        """Products below default threshold (10) appear in result."""
        with patch("smart_invoice_pro.api.dashboard_api.products_container") as mock_prod, \
             patch("smart_invoice_pro.api.dashboard_api.stock_container") as mock_stock:
            mock_prod.read_all_items.return_value = [
                {"id": "p1", "name": "Widget", "reorder_level": 10},
            ]
            # Stock: 3 IN, 0 OUT → current = 3, below threshold 10
            mock_stock.query_items.return_value = [
                {"type": "IN", "quantity": 3},
            ]

            resp = client.get("/api/dashboard/low-stock", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
            assert data[0]["product_id"] == "p1"
            assert data[0]["stock"] == 3

    def test_low_stock_custom_threshold(self, client, headers_a):
        """Custom threshold via query param."""
        with patch("smart_invoice_pro.api.dashboard_api.products_container") as mock_prod, \
             patch("smart_invoice_pro.api.dashboard_api.stock_container") as mock_stock:
            mock_prod.read_all_items.return_value = [
                {"id": "p1", "name": "Widget", "reorder_level": 5},
            ]
            mock_stock.query_items.return_value = [
                {"type": "IN", "quantity": 10},
            ]

            resp = client.get("/api/dashboard/low-stock?threshold=5", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            # Stock is 10, reorder_level is 5 → not low
            assert len(data) == 0

    def test_low_stock_empty(self, client, headers_a):
        """No products returns empty list."""
        with patch("smart_invoice_pro.api.dashboard_api.products_container") as mock_prod:
            mock_prod.read_all_items.return_value = []
            resp = client.get("/api/dashboard/low-stock", headers=headers_a)
            assert resp.status_code == 200
            assert resp.get_json() == []


class TestDashboardMonthlyRevenue:
    """GET /api/dashboard/monthly-revenue tests."""

    def test_monthly_revenue_returns_list(self, client, headers_a):
        """Returns monthly revenue buckets."""
        with patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv:
            mock_inv.read_all_items.return_value = []
            resp = client.get("/api/dashboard/monthly-revenue", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, list)

    def test_monthly_revenue_with_invoices(self, client, headers_a):
        """Invoices are bucketed by month."""
        with patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv:
            mock_inv.read_all_items.return_value = [
                {"total_amount": 1000, "created_at": "2026-03-15T00:00:00"},
                {"total_amount": 2000, "created_at": "2026-03-20T00:00:00"},
            ]
            resp = client.get("/api/dashboard/monthly-revenue", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, list)


class TestDashboardRecentInvoices:
    """GET /api/dashboard/recent-invoices tests."""

    def test_recent_invoices_default_limit(self, client, headers_a):
        """Returns recent invoices sorted by date."""
        with patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv:
            mock_inv.read_all_items.return_value = [
                {"id": "i1", "invoice_number": "INV-001", "customer_name": "Acme",
                 "total_amount": 1000, "status": "Issued", "issue_date": "2026-03-01",
                 "due_date": "2026-03-15", "created_at": "2026-03-01T00:00:00"},
            ]
            resp = client.get("/api/dashboard/recent-invoices", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
            assert data[0]["invoice_number"] == "INV-001"

    def test_recent_invoices_custom_limit(self, client, headers_a):
        """Limit query param controls count."""
        with patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv:
            invoices = [
                {"id": f"i{i}", "total_amount": 100, "status": "Draft",
                 "created_at": f"2026-03-{i+1:02d}T00:00:00",
                 "issue_date": f"2026-03-{i+1:02d}", "due_date": "2026-03-30"}
                for i in range(5)
            ]
            mock_inv.read_all_items.return_value = invoices
            resp = client.get("/api/dashboard/recent-invoices?limit=2", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 2

    def test_recent_invoices_empty(self, client, headers_a):
        """No invoices returns empty list."""
        with patch("smart_invoice_pro.api.dashboard_api.invoices_container") as mock_inv:
            mock_inv.read_all_items.return_value = []
            resp = client.get("/api/dashboard/recent-invoices", headers=headers_a)
            assert resp.status_code == 200
            assert resp.get_json() == []

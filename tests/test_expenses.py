"""Tests for expenses API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


SAMPLE_EXPENSE = {
    "vendor_name": "Office Supplies Inc",
    "date": "2026-03-15",
    "category": "Office Supplies",
    "amount": 250.0,
}

STORED_EXPENSE = {
    "id": "exp-001",
    "vendor_name": "Office Supplies Inc",
    "date": "2026-03-15",
    "category": "Office Supplies",
    "amount": 250.0,
    "currency": "INR",
    "notes": "",
    "receipt_url": None,
    "lifecycle_status": "ACTIVE",
    "created_at": "2026-03-15T00:00:00",
    "updated_at": "2026-03-15T00:00:00",
}


class TestCreateExpense:
    """POST /api/expenses tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            resp = client.post("/api/expenses", json=SAMPLE_EXPENSE, headers=headers_a)
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["vendor_name"] == "Office Supplies Inc"
            assert data["amount"] == 250.0

    def test_create_missing_vendor_name(self, client, headers_a):
        payload = {"date": "2026-03-15", "category": "Office", "amount": 100}
        resp = client.post("/api/expenses", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_missing_date(self, client, headers_a):
        payload = {"vendor_name": "X", "category": "Office", "amount": 100}
        resp = client.post("/api/expenses", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_missing_category(self, client, headers_a):
        payload = {"vendor_name": "X", "date": "2026-03-15", "amount": 100}
        resp = client.post("/api/expenses", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_missing_amount(self, client, headers_a):
        payload = {"vendor_name": "X", "date": "2026-03-15", "category": "Office"}
        resp = client.post("/api/expenses", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_defaults_currency_to_inr(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            resp = client.post("/api/expenses", json=SAMPLE_EXPENSE, headers=headers_a)
            assert resp.status_code == 201
            assert resp.get_json()["currency"] == "INR"


class TestListExpenses:
    """GET /api/expenses tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_EXPENSE]
            resp = client.get("/api/expenses", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/expenses", headers=headers_a)
            assert resp.status_code == 200


class TestGetExpense:
    """GET /api/expenses/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_EXPENSE]
            resp = client.get("/api/expenses/exp-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/expenses/nope", headers=headers_a)
            assert resp.status_code == 404


class TestUpdateExpense:
    """PUT /api/expenses/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_EXPENSE]
            mock_ctr.replace_item.return_value = {}
            resp = client.put("/api/expenses/exp-001", json={"notes": "Updated"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/expenses/nope", json={"notes": "x"}, headers=headers_a)
            assert resp.status_code == 404


class TestDeleteExpense:
    """DELETE /api/expenses/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_EXPENSE]
            mock_ctr.replace_item.return_value = {**STORED_EXPENSE, "lifecycle_status": "ARCHIVED"}
            resp = client.delete("/api/expenses/exp-001", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["message"] == "Expense archived successfully"
            mock_ctr.replace_item.assert_called_once()

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/expenses/nope", headers=headers_a)
            assert resp.status_code == 404


class TestExpenseDependencies:
    """GET /api/expenses/<id>/dependencies tests."""

    def test_dependencies_returns_no_deps(self, client, headers_a):
        resp = client.get("/api/expenses/exp-001/dependencies", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hasDependencies"] is False


class TestExpenseStats:
    """GET /api/expenses/stats/summary tests."""

    def test_stats_returns_summary(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = [
                {"amount": 100, "category": "Office Supplies"},
                {"amount": 200, "category": "Office Supplies"},
                {"amount": 300, "category": "Travel"},
            ]
            resp = client.get("/api/expenses/stats/summary", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_amount"] == 600
            assert data["total_count"] == 3
            assert data["average_amount"] == 200.0
            assert "Office Supplies" in data["by_category"]
            assert "Travel" in data["by_category"]

    def test_stats_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.expenses_api.expenses_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/expenses/stats/summary", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_amount"] == 0
            assert data["average_amount"] == 0

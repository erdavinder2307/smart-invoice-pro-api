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


class TestExportExpenses:
    """GET /api/expenses/export — CSV download tests."""

    @patch("smart_invoice_pro.api.expenses_api.expenses_container")
    def test_export_returns_csv(self, mock_ctr, client, headers_a):
        """Happy path — returns text/csv with correct header row and data row."""
        mock_ctr.query_items.return_value = [
            {
                "id": "exp-export-001",
                "tenant_id": TENANT_A,
                "vendor_name": "Staples",
                "category": "Office Supplies",
                "amount": 2500,
                "currency": "INR",
                "date": "2026-05-01",
                "status": "Pending",
                "payment_mode": "Cash",
                "paid_through": "Cash",
                "billable": False,
                "notes": "",
            }
        ]

        resp = client.get("/api/expenses/export", headers=headers_a)

        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        body = resp.data.decode("utf-8")
        assert "Date" in body
        assert "Vendor / Payee" in body
        assert "Staples" in body
        assert "Office Supplies" in body

    @patch("smart_invoice_pro.api.expenses_api.expenses_container")
    def test_export_empty_returns_header_row_only(self, mock_ctr, client, headers_a):
        """Empty result still returns a valid CSV with just the header row."""
        mock_ctr.query_items.return_value = []

        resp = client.get("/api/expenses/export", headers=headers_a)

        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        body = resp.data.decode("utf-8")
        assert "Date" in body
        lines = [line for line in body.strip().splitlines() if line]
        assert len(lines) == 1

    @patch("smart_invoice_pro.api.expenses_api.expenses_container")
    def test_export_respects_category_filter(self, mock_ctr, client, headers_a):
        """Category filter param is wired into the Cosmos query."""
        mock_ctr.query_items.return_value = []

        resp = client.get("/api/expenses/export?category=Travel", headers=headers_a)

        assert resp.status_code == 200
        call_kwargs = mock_ctr.query_items.call_args[1]
        params_list = call_kwargs.get("parameters", [])
        param_values = [p["value"] for p in params_list]
        assert "Travel" in param_values

    @patch("smart_invoice_pro.api.expenses_api.expenses_container")
    def test_export_content_disposition_header(self, mock_ctr, client, headers_a):
        """Response includes Content-Disposition attachment header."""
        mock_ctr.query_items.return_value = []

        resp = client.get("/api/expenses/export", headers=headers_a)

        assert resp.status_code == 200
        disposition = resp.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert "expenses-export.csv" in disposition

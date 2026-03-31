"""
Tests for Bank Reconciliation API (bank_reconciliation_api.py).
Upload, list, match/unmatch, create-expense, auto-match, delete, matchable.
"""
import io
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


TXN_DOC = {
    "id": "txn-001",
    "user_id": USER_A,
    "bank_account_id": "ba-001",
    "date": "2024-06-15",
    "description": "Payment from Acme",
    "amount": 1000.0,
    "match_status": "unmatched",
    "match_type": None,
    "match_id": None,
}


def _patches():
    """Patch all bank_reconciliation_api containers."""
    return (
        patch("smart_invoice_pro.api.bank_reconciliation_api.bank_txns_container"),
        patch("smart_invoice_pro.api.bank_reconciliation_api.invoices_container"),
        patch("smart_invoice_pro.api.bank_reconciliation_api.expenses_container"),
    )


class TestUploadStatement:
    """POST /api/reconciliation/upload"""

    def test_csv_upload_success(self, client, headers_a):
        csv_content = "date,description,debit,credit,balance\n2024-01-15,Acme Payment,,1000.00,5000.00\n"
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3:
            mock_txns.create_item.return_value = {}
            mock_inv.query_items.return_value = []  # no auto-match
            data = {
                "file": (io.BytesIO(csv_content.encode()), "statement.csv"),
                "bank_account_id": "ba-001",
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 201
        result = resp.get_json()
        assert result["imported"] >= 1

    def test_no_file_returns_400(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = client.post("/api/reconciliation/upload",
                               data={"bank_account_id": "ba-001"},
                               content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 400

    def test_unsupported_format(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            data = {
                "file": (io.BytesIO(b"fake"), "statement.xlsx"),
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 400

    def test_empty_csv_returns_422(self, client, headers_a):
        csv_content = "date,description,debit,credit,balance\n"
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            data = {
                "file": (io.BytesIO(csv_content.encode()), "empty.csv"),
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 422


class TestListTransactions:
    """GET /api/reconciliation/transactions"""

    def test_list_all(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC]
            resp = client.get("/api/reconciliation/transactions", headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_filter_by_status(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.get("/api/reconciliation/transactions?status=matched",
                              headers=headers_a)
        assert resp.status_code == 200


class TestMatchTransaction:
    """POST /api/reconciliation/<txn_id>/match"""

    def test_manual_match_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_txns.replace_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/match",
                               json={"match_type": "invoice", "match_id": "inv-123"},
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["match_status"] == "matched"
        assert data["match_type"] == "invoice"

    def test_match_missing_fields(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = client.post("/api/reconciliation/txn-001/match",
                               json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_match_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/match",
                               json={"match_type": "invoice", "match_id": "inv-123"},
                               headers=headers_a)
        assert resp.status_code == 404


class TestUnmatchTransaction:
    """POST /api/reconciliation/<txn_id>/unmatch"""

    def test_unmatch_success(self, client, headers_a):
        matched_txn = {**TXN_DOC, "match_status": "matched", "match_type": "invoice", "match_id": "inv-123"}
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [matched_txn.copy()]
            mock_txns.replace_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/unmatch",
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["match_status"] == "unmatched"
        assert data["match_type"] is None

    def test_unmatch_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/unmatch",
                               headers=headers_a)
        assert resp.status_code == 404


class TestCreateExpenseFromTxn:
    """POST /api/reconciliation/<txn_id>/create-expense"""

    def test_create_expense_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3 as mock_exp:
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_txns.replace_item.return_value = {}
            mock_exp.create_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/create-expense",
                               json={"vendor_name": "Acme", "category": "Office"},
                               headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["expense"]["vendor_name"] == "Acme"
        assert data["expense"]["category"] == "Office"
        assert data["transaction"]["match_status"] == "matched"

    def test_create_expense_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/create-expense",
                               json={"vendor_name": "X"}, headers=headers_a)
        assert resp.status_code == 404


class TestAutoMatch:
    """POST /api/reconciliation/auto-match"""

    def test_auto_match_no_unmatched(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/auto-match", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["processed"] == 0
        assert data["newly_matched"] == 0

    def test_auto_match_with_invoice(self, client, headers_a):
        """An unmatched txn with amount matching an invoice's balance_due gets matched."""
        unmatched = TXN_DOC.copy()
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3:
            mock_txns.query_items.return_value = [unmatched]
            mock_txns.replace_item.return_value = {}
            mock_inv.query_items.return_value = [
                {"id": "inv-100", "invoice_number": "INV-100", "balance_due": 1000.0, "customer_id": "c1"}
            ]
            resp = client.post("/api/reconciliation/auto-match", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["newly_matched"] == 1


class TestDeleteTransaction:
    """DELETE /api/reconciliation/<txn_id>"""

    def test_delete_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC]
            resp = client.delete("/api/reconciliation/txn-001", headers=headers_a)
        assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.delete("/api/reconciliation/bad-id", headers=headers_a)
        assert resp.status_code == 404


class TestGetMatchable:
    """GET /api/reconciliation/matchable"""

    def test_matchable_invoices(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2 as mock_inv, p3:
            mock_inv.query_items.return_value = [
                {"id": "inv-1", "invoice_number": "INV-001", "balance_due": 500, "status": "Issued"}
            ]
            resp = client.get("/api/reconciliation/matchable?type=invoice",
                              headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_matchable_expenses(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_exp:
            mock_exp.query_items.return_value = [
                {"id": "exp-1", "vendor_name": "Vendor A", "amount": 200}
            ]
            resp = client.get("/api/reconciliation/matchable?type=expense",
                              headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

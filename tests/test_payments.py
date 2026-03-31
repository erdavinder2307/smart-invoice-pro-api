"""
Tests for Invoice record-payment endpoint and payments API.
"""
import copy
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import TENANT_A, TENANT_B, USER_A


# ─────────────────────────────────────────────────────────────────────────────
#  RECORD PAYMENT (POST /api/invoices/<id>/record-payment)
# ─────────────────────────────────────────────────────────────────────────────
class TestRecordPayment:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_full_payment_marks_paid(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={
                "amount": 1180.0,
                "payment_mode": "Bank Transfer",
                "payment_date": "2025-06-10",
            },
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["invoice"]["status"] == "Paid"
        assert data["invoice"]["balance_due"] == 0.0
        assert data["invoice"]["amount_paid"] == 1180.0

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_partial_payment_updates_balance(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={
                "amount": 500.0,
                "payment_mode": "Cash",
                "payment_date": "2025-06-10",
            },
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["invoice"]["amount_paid"] == 500.0
        assert data["invoice"]["balance_due"] == 680.0
        assert data["invoice"]["status"] == "Partially Paid"

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_overpayment_rejected(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={
                "amount": 99999.0,
                "payment_mode": "Bank Transfer",
                "payment_date": "2025-06-10",
            },
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "exceeds" in resp.get_json()["error"].lower() or "exceeds" in str(resp.get_json().get("details", "")).lower()

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_zero_amount_rejected(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={
                "amount": 0,
                "payment_mode": "Cash",
                "payment_date": "2025-06-10",
            },
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "greater than zero" in str(resp.get_json()).lower()

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_negative_amount_rejected(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={
                "amount": -100,
                "payment_mode": "Cash",
                "payment_date": "2025-06-10",
            },
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_missing_required_fields(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": 100},
            headers=headers_a,
        )
        assert resp.status_code == 400
        details = resp.get_json().get("details", {})
        assert "payment_mode" in details
        assert "payment_date" in details

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_payment_on_nonexistent_invoice(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.post(
            "/api/invoices/nonexistent/record-payment",
            json={"amount": 100, "payment_mode": "Cash", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_payment_on_cancelled_invoice(self, mock_inv, client, headers_a, stored_invoice_a):
        inv = copy.deepcopy(stored_invoice_a)
        inv["status"] = "Cancelled"
        mock_inv.query_items.return_value = [inv]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": 100, "payment_mode": "Cash", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "cancelled" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_payment_appends_to_history(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": 200, "payment_mode": "UPI", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        data = resp.get_json()
        history = data["invoice"].get("payment_history", [])
        assert len(history) == 1
        assert history[0]["amount"] == 200.0
        assert history[0]["payment_mode"] == "UPI"

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_no_body_returns_400(self, mock_inv, client, headers_a):
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            data="",
            content_type="application/json",
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_invalid_amount_type(self, mock_inv, client, headers_a, stored_invoice_a):
        mock_inv.query_items.return_value = [copy.deepcopy(stored_invoice_a)]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": "not-a-number", "payment_mode": "Cash", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "number" in str(resp.get_json()).lower()


# ─────────────────────────────────────────────────────────────────────────────
#  PAYMENT STATUS FLOW
# ─────────────────────────────────────────────────────────────────────────────
class TestPaymentStatusFlow:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_draft_to_partially_paid(self, mock_inv, client, headers_a, stored_invoice_a):
        inv = copy.deepcopy(stored_invoice_a)
        inv["status"] = "Draft"
        mock_inv.query_items.return_value = [inv]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": 100, "payment_mode": "Cash", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["invoice"]["status"] == "Partially Paid"

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_issued_to_paid_on_full_payment(self, mock_inv, client, headers_a, stored_invoice_a):
        inv = copy.deepcopy(stored_invoice_a)
        inv["status"] = "Issued"
        mock_inv.query_items.return_value = [inv]
        resp = client.post(
            "/api/invoices/inv-aaa-001/record-payment",
            json={"amount": 1180, "payment_mode": "Bank Transfer", "payment_date": "2025-06-10"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["invoice"]["status"] == "Paid"


# ─────────────────────────────────────────────────────────────────────────────
#  PAYMENTS API (Zoho integration)
# ─────────────────────────────────────────────────────────────────────────────
class TestPaymentsAPI:

    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_transactions_list(self, mock_pay, client, headers_a):
        mock_pay.query_items.return_value = [
            {"id": "txn-1", "amount": 1000, "status": "paid"},
        ]
        resp = client.get("/api/payments/transactions?user_id=user-aaa-1111", headers=headers_a)
        assert resp.status_code == 200

    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_transactions_missing_user_id(self, mock_pay, client, headers_a):
        resp = client.get("/api/payments/transactions", headers=headers_a)
        assert resp.status_code == 400
        assert "user_id" in resp.get_json()["error"]

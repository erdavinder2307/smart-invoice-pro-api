"""Tests for payments_api.py – Zoho Payments create-session, webhook, transactions, status."""
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import TENANT_A, USER_A


SAMPLE_INVOICE = {
    "id": "inv-001",
    "invoice_number": "INV-00001",
    "customer_id": "cust-001",
    "status": "Issued",
    "total_amount": 1000.0,
    "balance_due": 1000.0,
    "currency": "INR",
}

SAMPLE_TXN = {
    "id": "txn-001",
    "user_id": USER_A,
    "invoice_id": "inv-001",
    "invoice_number": "INV-00001",
    "amount": 1000.0,
    "currency": "INR",
    "status": "pending",
    "payment_provider": "zoho_payments",
    "payment_link_id": "pl-001",
    "payment_url": "https://payments.zoho.com/pl-001",
}


class TestCreatePaymentSession:
    """POST /payments/create-session"""

    @patch("smart_invoice_pro.api.payments_api.payments_container")
    @patch("smart_invoice_pro.api.payments_api.requests")
    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    def test_success(self, mock_inv, mock_requests, mock_pay, client, headers_a):
        mock_inv.query_items.return_value = [SAMPLE_INVOICE.copy()]

        # Mock Zoho token exchange
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "tok-123"}
        token_resp.raise_for_status = MagicMock()

        # Mock Zoho payment link creation
        link_resp = MagicMock()
        link_resp.json.return_value = {
            "payment_link_id": "pl-new",
            "short_url": "https://pay.zoho.com/short",
        }
        link_resp.raise_for_status = MagicMock()

        mock_requests.post.side_effect = [token_resp, link_resp]
        mock_requests.HTTPError = Exception

        resp = client.post(
            "/api/payments/create-session",
            json={"invoice_id": "inv-001", "user_id": USER_A},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["payment_link_id"] == "pl-new"
        assert data["amount"] == 1000.0
        mock_pay.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    def test_missing_params(self, mock_inv, client, headers_a):
        resp = client.post("/api/payments/create-session", json={}, headers=headers_a)
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    def test_invoice_not_found(self, mock_inv, client, headers_a):
        mock_inv.query_items.return_value = []
        resp = client.post(
            "/api/payments/create-session",
            json={"invoice_id": "nope", "user_id": USER_A},
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    def test_already_paid(self, mock_inv, client, headers_a):
        inv = {**SAMPLE_INVOICE, "status": "Paid"}
        mock_inv.query_items.return_value = [inv]
        resp = client.post(
            "/api/payments/create-session",
            json={"invoice_id": "inv-001", "user_id": USER_A},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "already paid" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.payments_api.requests")
    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    def test_zoho_api_error(self, mock_inv, mock_requests, client, headers_a):
        mock_inv.query_items.return_value = [SAMPLE_INVOICE.copy()]

        # Token succeeds but link creation raises HTTPError
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "tok"}
        token_resp.raise_for_status = MagicMock()

        mock_requests.post.side_effect = [token_resp, Exception("Zoho down")]
        mock_requests.HTTPError = Exception

        resp = client.post(
            "/api/payments/create-session",
            json={"invoice_id": "inv-001", "user_id": USER_A},
            headers=headers_a,
        )
        assert resp.status_code in (500, 502)


class TestZohoWebhook:
    """POST /payments/webhook"""

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_payment_success_event(self, mock_pay, mock_inv, client, headers_a):
        mock_pay.query_items.return_value = [SAMPLE_TXN.copy()]
        mock_inv.query_items.return_value = [SAMPLE_INVOICE.copy()]

        resp = client.post(
            "/api/payments/webhook",
            json={
                "event_type": "payment.success",
                "data": {
                    "reference_id": "inv-001",
                    "payment_link_id": "pl-001",
                    "amount": 1000.0,
                    "transaction_id": "zoho-txn-1",
                },
            },
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        # Transaction updated to paid
        mock_pay.replace_item.assert_called_once()
        replaced_txn = mock_pay.replace_item.call_args[1]["body"]
        assert replaced_txn["status"] == "paid"
        assert replaced_txn["zoho_txn_id"] == "zoho-txn-1"

        # Invoice updated to Paid
        mock_inv.replace_item.assert_called_once()
        replaced_inv = mock_inv.replace_item.call_args[1]["body"]
        assert replaced_inv["status"] == "Paid"

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_payment_link_paid_event(self, mock_pay, mock_inv, client, headers_a):
        mock_pay.query_items.return_value = [SAMPLE_TXN.copy()]
        mock_inv.query_items.return_value = [SAMPLE_INVOICE.copy()]

        resp = client.post(
            "/api/payments/webhook",
            json={
                "event_type": "payment_link.paid",
                "data": {
                    "reference_id": "inv-001",
                    "payment_link_id": "pl-001",
                    "amount_paid": 1000.0,
                    "payment_id": "zoho-pid",
                },
            },
            headers=headers_a,
        )
        assert resp.status_code == 200

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_unknown_event_ignored(self, mock_pay, mock_inv, client, headers_a):
        resp = client.post(
            "/api/payments/webhook",
            json={"event_type": "payment.refunded", "data": {}},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
        mock_pay.replace_item.assert_not_called()

    @patch("smart_invoice_pro.api.payments_api.invoices_container")
    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_no_matching_transaction(self, mock_pay, mock_inv, client, headers_a):
        mock_pay.query_items.return_value = []
        mock_inv.query_items.return_value = []
        resp = client.post(
            "/api/payments/webhook",
            json={
                "event_type": "payment.success",
                "data": {"reference_id": "inv-999", "payment_link_id": "pl-999"},
            },
            headers=headers_a,
        )
        assert resp.status_code == 200


class TestPaymentStatus:
    """GET /payments/status/<transaction_id>"""

    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_found(self, mock_pay, client, headers_a):
        mock_pay.query_items.return_value = [SAMPLE_TXN.copy()]
        resp = client.get("/api/payments/status/txn-001", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["id"] == "txn-001"

    @patch("smart_invoice_pro.api.payments_api.payments_container")
    def test_not_found(self, mock_pay, client, headers_a):
        mock_pay.query_items.return_value = []
        resp = client.get("/api/payments/status/nope", headers=headers_a)
        assert resp.status_code == 404

"""
Cross-cutting tenant isolation tests.

For EVERY module that stores data per-tenant, verify:
  1. User A cannot READ User B's data
  2. User A cannot UPDATE User B's data
  3. User A cannot DELETE User B's data
"""
from unittest.mock import patch, MagicMock
import copy

import pytest

from tests.conftest import TENANT_A, TENANT_B, auth_headers


# ── Helpers ──────────────────────────────────────────────────────────────────
def _headers_a():
    return auth_headers(tenant_id=TENANT_A)

def _headers_b():
    return auth_headers(tenant_id=TENANT_B)


_INVOICE_A = {
    "id": "inv-iso-001",
    "invoice_number": "INV-ISO",
    "customer_id": "cust-001",
    "issue_date": "2025-06-01",
    "due_date": "2025-06-15",
    "subtotal": 1000,
    "total_amount": 1180,
    "amount_paid": 0,
    "balance_due": 1180,
    "status": "Issued",
    "tenant_id": TENANT_A,
    "items": [],
    "payment_history": [],
    "created_at": "2025-06-01T00:00:00",
    "updated_at": "2025-06-01T00:00:00",
}

_CUSTOMER_A = {
    "id": "cust-iso-001",
    "customer_id": "cust-pk-iso",
    "display_name": "Isolated Customer",
    "email": "iso@test.com",
    "phone": "9876543210",
    "tenant_id": TENANT_A,
}

_PRODUCT_A = {
    "id": "prod-iso-001",
    "product_id": "prod-pk-iso",
    "name": "Isolated Product",
    "price": 99,
    "unit": "Nos",
    "tenant_id": TENANT_A,
    "is_deleted": False,
}


class TestInvoiceTenantIsolation:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_get_invoice_forbidden(self, mock_inv, client):
        mock_inv.query_items.return_value = [_INVOICE_A]
        resp = client.get("/api/invoices/inv-iso-001", headers=_headers_b())
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_update_invoice_forbidden(self, mock_inv, client):
        mock_inv.query_items.return_value = [_INVOICE_A]
        resp = client.put(
            "/api/invoices/inv-iso-001",
            json={"status": "Cancelled"},
            headers=_headers_b(),
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_patch_invoice_forbidden(self, mock_inv, client):
        mock_inv.query_items.return_value = [_INVOICE_A]
        resp = client.patch(
            "/api/invoices/inv-iso-001",
            json={"status": "Cancelled"},
            headers=_headers_b(),
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_delete_invoice_forbidden(self, mock_inv, client):
        mock_inv.query_items.return_value = [_INVOICE_A]
        resp = client.delete("/api/invoices/inv-iso-001", headers=_headers_b())
        assert resp.status_code == 403
        mock_inv.delete_item.assert_not_called()


class TestCustomerTenantIsolation:

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_get_customer_forbidden(self, mock_cust, client):
        mock_cust.query_items.return_value = [_CUSTOMER_A]
        resp = client.get("/api/customers/cust-iso-001", headers=_headers_b())
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_update_customer_forbidden(self, mock_cust, client):
        mock_cust.query_items.return_value = [_CUSTOMER_A]
        resp = client.put(
            "/api/customers/cust-iso-001",
            json={"display_name": "Hacked"},
            headers=_headers_b(),
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.customers_api.customers_container")
    def test_delete_customer_forbidden(self, mock_cust, client):
        mock_cust.query_items.return_value = [_CUSTOMER_A]
        resp = client.delete("/api/customers/cust-iso-001", headers=_headers_b())
        assert resp.status_code == 403
        mock_cust.delete_item.assert_not_called()


class TestProductTenantIsolation:

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_update_product_forbidden(self, mock_prod, client):
        mock_prod.query_items.return_value = [_PRODUCT_A]
        resp = client.put(
            "/api/products/prod-iso-001",
            json={"name": "Hacked"},
            headers=_headers_b(),
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.product_api._item_used_in_invoices")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_delete_product_forbidden(self, mock_prod, mock_used, client):
        mock_prod.query_items.return_value = [_PRODUCT_A]
        resp = client.delete("/api/products/prod-iso-001", headers=_headers_b())
        assert resp.status_code == 403


class TestRecordPaymentTenantIsolation:

    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_record_payment_cross_tenant(self, mock_inv, client):
        """Record-payment uses tenant_id in its query, so no result → 404."""
        mock_inv.query_items.return_value = []  # Nothing matches tenant B + invoice ID
        resp = client.post(
            "/api/invoices/inv-iso-001/record-payment",
            json={"amount": 100, "payment_mode": "Cash", "payment_date": "2025-06-10"},
            headers=_headers_b(),
        )
        assert resp.status_code == 404

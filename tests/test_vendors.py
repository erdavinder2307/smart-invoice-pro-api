"""Tests for vendors API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


SAMPLE_VENDOR = {
    "vendor_name": "ABC Suppliers",
    "contact_person": "John Doe",
    "email": "john@abc.com",
    "phone": "9876543210",
}

STORED_VENDOR = {
    "id": "v-001",
    "vendor_id": "v-001",
    "vendor_name": "ABC Suppliers",
    "contact_person": "John Doe",
    "email": "john@abc.com",
    "phone": "9876543210",
    "payment_terms": "Net 30",
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreateVendor:
    """POST /api/vendors tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.create_item.return_value = {**SAMPLE_VENDOR, "id": "new-id"}
            resp = client.post("/api/vendors", json=SAMPLE_VENDOR, headers=headers_a)
            assert resp.status_code == 201

    def test_create_missing_name(self, client, headers_a):
        resp = client.post("/api/vendors", json={"contact_person": "X"}, headers=headers_a)
        assert resp.status_code == 400

    def test_create_missing_contact_person(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.create_item.return_value = {"id": "new-id", "vendor_name": "X"}
            resp = client.post("/api/vendors", json={"vendor_name": "X"}, headers=headers_a)
            assert resp.status_code == 201

    def test_create_invalid_email(self, client, headers_a):
        payload = {**SAMPLE_VENDOR, "email": "not-an-email"}
        resp = client.post("/api/vendors", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_defaults_payment_terms(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            client.post("/api/vendors", json=SAMPLE_VENDOR, headers=headers_a)
            call_args = mock_ctr.create_item.call_args
            body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
            assert body["payment_terms"] == "Net 30"


class TestListVendors:
    """GET /api/vendors tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            resp = client.get("/api/vendors", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/vendors", headers=headers_a)
            assert resp.status_code == 200

    def test_list_with_meta(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_vendors, \
             patch("smart_invoice_pro.api.vendors_api.bills_container") as mock_bills:
            mock_vendors.query_items.return_value = [STORED_VENDOR]
            mock_bills.query_items.return_value = [
                {
                    "vendor_id": "v-001",
                    "total_amount": 5000,
                    "balance_due": 1200,
                    "bill_date": "2026-03-10",
                    "created_at": "2026-03-10T00:00:00",
                }
            ]
            resp = client.get("/api/vendors?include_meta=1&page=1&page_size=10", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data.get("data"), list)
            assert data.get("total") == 1
            assert data.get("summary", {}).get("vendors_with_payables") == 1


class TestGetVendor:
    """GET /api/vendors/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            resp = client.get("/api/vendors/v-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/vendors/nope", headers=headers_a)
            assert resp.status_code == 404


class TestUpdateVendor:
    """PUT /api/vendors/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            mock_ctr.replace_item.return_value = {**STORED_VENDOR, "vendor_name": "Updated Corp"}
            resp = client.put("/api/vendors/v-001", json={"vendor_name": "Updated Corp"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/vendors/nope", json={"vendor_name": "X"}, headers=headers_a)
            assert resp.status_code == 404

    def test_update_invalid_email(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            resp = client.put("/api/vendors/v-001", json={"email": "bad"}, headers=headers_a)
            assert resp.status_code == 400


class TestDeleteVendor:
    """DELETE /api/vendors/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            resp = client.delete("/api/vendors/v-001", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/vendors/nope", headers=headers_a)
            assert resp.status_code == 404


class TestBulkVendors:
    """POST /api/vendors/bulk tests."""

    def test_bulk_mark_inactive(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            active = {**STORED_VENDOR, "status": "Active"}
            mock_ctr.query_items.return_value = [active]
            resp = client.post(
                "/api/vendors/bulk",
                json={"action": "mark_inactive", "ids": ["v-001"]},
                headers=headers_a,
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["updated"] == 1

    def test_bulk_delete(self, client, headers_a):
        with patch("smart_invoice_pro.api.vendors_api.vendors_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_VENDOR]
            resp = client.post(
                "/api/vendors/bulk",
                json={"action": "delete", "ids": ["v-001"]},
                headers=headers_a,
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["deleted"] == 1

    def test_bulk_invalid_action(self, client, headers_a):
        resp = client.post(
            "/api/vendors/bulk",
            json={"action": "archive", "ids": ["v-001"]},
            headers=headers_a,
        )
        assert resp.status_code == 400

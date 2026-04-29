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

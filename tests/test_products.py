"""
Tests for Product API — CRUD, validation, soft-delete, duplicate detection.
"""
from unittest.mock import patch, MagicMock
import copy

import pytest

from tests.conftest import TENANT_A, TENANT_B


class TestCreateProduct:

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_product_success(self, mock_prod, client, headers_a, sample_product):
        mock_prod.query_items.return_value = []  # no duplicate
        resp = client.post("/api/products", json=sample_product, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Widget Pro"
        assert data["is_deleted"] is False
        mock_prod.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_product_duplicate_name(self, mock_prod, client, headers_a, sample_product):
        mock_prod.query_items.return_value = [{"id": "existing-id"}]
        resp = client.post("/api/products", json=sample_product, headers=headers_a)
        assert resp.status_code == 400
        assert "already exists" in resp.get_json()["error"]
        assert resp.get_json()["field"] == "name"

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_product_empty_name(self, mock_prod, client, headers_a):
        resp = client.post(
            "/api/products",
            json={"name": "", "price": 100, "unit": "Nos"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_product_negative_price(self, mock_prod, client, headers_a):
        mock_prod.query_items.return_value = []
        resp = client.post(
            "/api/products",
            json={"name": "BadPrice", "price": -10, "unit": "Nos"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "negative" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_product_price_exceeds_max(self, mock_prod, client, headers_a):
        mock_prod.query_items.return_value = []
        resp = client.post(
            "/api/products",
            json={"name": "Expensive", "price": 100_000_000, "unit": "Nos"},
            headers=headers_a,
        )
        assert resp.status_code == 400
        assert "exceed" in resp.get_json()["error"].lower()


class TestListProducts:

    @patch("smart_invoice_pro.api.product_api.get_container")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_list_excludes_soft_deleted(self, mock_prod, mock_gc, client, headers_a):
        mock_prod.query_items.return_value = [
            {"id": "1", "name": "Active", "is_deleted": False, "tenant_id": TENANT_A},
            {"id": "2", "name": "Deleted", "is_deleted": True, "tenant_id": TENANT_A},
        ]
        mock_stock = MagicMock()
        mock_stock.query_items.return_value = []
        mock_gc.return_value = mock_stock

        resp = client.get("/api/products", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Active"


class TestGetProduct:

    @patch("smart_invoice_pro.api.product_api.get_container")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_get_product_success(self, mock_prod, mock_gc, client, headers_a, stored_product_a):
        mock_prod.query_items.return_value = [stored_product_a]
        mock_gc.return_value = MagicMock(query_items=MagicMock(return_value=[]))
        resp = client.get("/api/products/prod-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Widget Pro"

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_get_product_not_found(self, mock_prod, client, headers_a):
        mock_prod.query_items.return_value = []
        resp = client.get("/api/products/nonexistent", headers=headers_a)
        assert resp.status_code == 404


class TestUpdateProduct:

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_update_product_success(self, mock_prod, client, headers_a, stored_product_a):
        # First query for finding item, second for duplicate check
        mock_prod.query_items.side_effect = [
            [stored_product_a],  # find by id
            [],                  # no duplicate name
        ]
        resp = client.put(
            "/api/products/prod-aaa-001",
            json={"name": "Widget Pro V2", "price": 600},
            headers=headers_a,
        )
        assert resp.status_code == 200

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_update_product_cross_tenant(self, mock_prod, client, headers_b, stored_product_a):
        mock_prod.query_items.return_value = [stored_product_a]
        resp = client.put(
            "/api/products/prod-aaa-001",
            json={"name": "Hacked"},
            headers=headers_b,
        )
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_update_product_not_found(self, mock_prod, client, headers_a):
        mock_prod.query_items.return_value = []
        resp = client.put(
            "/api/products/nonexistent",
            json={"name": "X"},
            headers=headers_a,
        )
        assert resp.status_code == 404


class TestDeleteProduct:

    @patch("smart_invoice_pro.api.product_api._item_used_in_invoices")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_soft_delete_success(self, mock_prod, mock_used, client, headers_a, stored_product_a):
        mock_prod.query_items.return_value = [stored_product_a]
        mock_used.return_value = 0
        resp = client.delete("/api/products/prod-aaa-001", headers=headers_a)
        assert resp.status_code == 200
        # Verify replace_item was called with is_deleted=True
        mock_prod.replace_item.assert_called_once()
        body = mock_prod.replace_item.call_args[1]["body"]
        assert body["is_deleted"] is True
        assert body["deleted_at"] is not None

    @patch("smart_invoice_pro.api.product_api._item_used_in_invoices")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_delete_product_used_in_invoices(self, mock_prod, mock_used, client, headers_a, stored_product_a):
        mock_prod.query_items.return_value = [stored_product_a]
        mock_used.return_value = 3
        resp = client.delete("/api/products/prod-aaa-001", headers=headers_a)
        assert resp.status_code == 400
        assert "used in 3 invoice(s)" in resp.get_json()["error"]

    @patch("smart_invoice_pro.api.product_api._item_used_in_invoices")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_delete_cross_tenant(self, mock_prod, mock_used, client, headers_b, stored_product_a):
        mock_prod.query_items.return_value = [stored_product_a]
        resp = client.delete("/api/products/prod-aaa-001", headers=headers_b)
        assert resp.status_code == 403


class TestResponseSanitization:

    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_create_excludes_cosmos_fields(self, mock_prod, client, headers_a, sample_product):
        mock_prod.query_items.return_value = []
        resp = client.post("/api/products", json=sample_product, headers=headers_a)
        data = resp.get_json()
        for field in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            assert field not in data

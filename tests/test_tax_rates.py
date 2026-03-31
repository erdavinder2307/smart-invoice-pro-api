"""
Tests for Tax Rates API (tax_rates_api.py).
GET/POST/PUT/DELETE /api/settings/taxes
POST /api/invoices/calculate-tax
"""
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, USER_A


ADMIN_USER = {
    "id": USER_A,
    "username": "admin",
    "role": "Admin",
}

SAMPLE_RATE = {
    "id": "rate-001",
    "tenant_id": TENANT_A,
    "name": "GST 18%",
    "rate": 18.0,
    "type": "GST",
    "components": {"cgst": 9.0, "sgst": 9.0, "igst": 18.0},
    "is_active": True,
    "is_default": True,
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
}


def _mock_tax_container():
    return MagicMock()


class TestListTaxRates:
    """GET /api/settings/taxes"""

    def test_list_existing_rates(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.query_items.return_value = [SAMPLE_RATE]
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr):
            resp = client.get("/api/settings/taxes", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "GST 18%"

    def test_seeds_defaults_when_empty(self, client, headers_a):
        """First call seeds 6 default GST slabs."""
        mock_ctr = _mock_tax_container()
        # First query: empty → seeds, second query after seed: still returns empty
        # But actually _seed_default_rates returns the seeded list directly
        mock_ctr.query_items.return_value = []
        mock_ctr.create_item.return_value = {}
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr):
            resp = client.get("/api/settings/taxes", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 6  # 6 default GST slabs
        assert mock_ctr.create_item.call_count == 6


class TestCreateTaxRate:
    """POST /api/settings/taxes (Admin only)"""

    def test_create_success(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.create_item.return_value = {}
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "name": "Custom 15%",
                "rate": 15.0,
                "type": "GST",
            }, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Custom 15%"
        assert data["rate"] == 15.0
        assert data["tenant_id"] == TENANT_A
        # Auto-derived components
        assert data["components"]["cgst"] == 7.5
        assert data["components"]["igst"] == 15.0

    def test_missing_name(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "rate": 10.0,
                "type": "GST",
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    def test_invalid_rate_value(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "name": "Bad Rate",
                "rate": 150.0,
                "type": "GST",
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "rate" in resp.get_json()["error"].lower()

    def test_invalid_rate_not_number(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "name": "Bad Rate",
                "rate": "abc",
                "type": "GST",
            }, headers=headers_a)
        assert resp.status_code == 400

    def test_invalid_type(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "name": "Bad Type",
                "rate": 10.0,
                "type": "VAT",
            }, headers=headers_a)
        assert resp.status_code == 400
        assert "type" in resp.get_json()["error"].lower()

    def test_exempt_type_zeroes_components(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.create_item.return_value = {}
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.post("/api/settings/taxes", json={
                "name": "Exempt",
                "rate": 0.0,
                "type": "Exempt",
            }, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["components"]["cgst"] == 0.0
        assert data["components"]["igst"] == 0.0


class TestUpdateTaxRate:
    """PUT /api/settings/taxes/<id> (Admin only)"""

    def test_update_success(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.query_items.return_value = [SAMPLE_RATE.copy()]
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.put("/api/settings/taxes/rate-001", json={
                "name": "Updated 18%",
                "rate": 18.0,
            }, headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated 18%"

    def test_update_not_found(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.query_items.return_value = []
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.put("/api/settings/taxes/bad-id", json={
                "name": "X",
            }, headers=headers_a)
        assert resp.status_code == 404


class TestDeleteTaxRate:
    """DELETE /api/settings/taxes/<id> (Admin only) — soft delete."""

    def test_soft_delete(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        rate = SAMPLE_RATE.copy()
        mock_ctr.query_items.return_value = [rate]
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.delete("/api/settings/taxes/rate-001", headers=headers_a)
        assert resp.status_code == 200
        # Verify the item was upserted with is_active=False
        upserted = mock_ctr.upsert_item.call_args[0][0]
        assert upserted["is_active"] is False

    def test_delete_not_found(self, client, headers_a):
        mock_ctr = _mock_tax_container()
        mock_ctr.query_items.return_value = []
        with patch("smart_invoice_pro.api.tax_rates_api._get_tax_rates_container",
                    return_value=mock_ctr), \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.delete("/api/settings/taxes/bad-id", headers=headers_a)
        assert resp.status_code == 404


class TestCalculateGst:
    """Unit tests for the calculate_gst() function."""

    def test_intra_state_cgst_sgst(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Delhi", "regular", True)
        assert result["tax_type"] == "CGST_SGST"
        assert result["is_intra_state"] is True
        assert result["cgst_amount"] == 90.0
        assert result["sgst_amount"] == 90.0
        assert result["igst_amount"] == 0.0
        assert result["total_tax"] == 180.0

    def test_inter_state_igst(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 2, "rate": 500, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Maharashtra", "regular", True)
        assert result["tax_type"] == "IGST"
        assert result["is_intra_state"] is False
        assert result["igst_amount"] == 180.0
        assert result["cgst_amount"] == 0.0

    def test_not_gst_applicable(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Delhi", "regular", False)
        assert result["tax_type"] == "NONE"
        assert result["total_tax"] == 0.0

    def test_sez_zero_rated(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Delhi", "special_economic_zone", True)
        assert result["tax_type"] == "NONE"
        assert result["total_tax"] == 0.0

    def test_composition_zero_rated(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Delhi", "composition", True)
        assert result["tax_type"] == "NONE"
        assert result["total_tax"] == 0.0

    def test_discount_reduces_base(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "discount": 200, "tax": 18}]
        result = calculate_gst(items, "Delhi", "Delhi", "regular", True)
        # base = 1*1000 - 200 = 800, cgst = 800*9/100 = 72
        assert result["cgst_amount"] == 72.0
        assert result["sgst_amount"] == 72.0
        assert result["total_tax"] == 144.0

    def test_place_of_supply_overrides_customer_state(self):
        from smart_invoice_pro.api.tax_rates_api import calculate_gst
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        # Customer is in Delhi but place of supply is Maharashtra → inter-state
        result = calculate_gst(items, "Delhi", "Delhi", "regular", True,
                               place_of_supply="Maharashtra")
        assert result["is_intra_state"] is False
        assert result["tax_type"] == "IGST"

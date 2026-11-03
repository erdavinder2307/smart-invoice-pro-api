"""Tests for tenant provisioning helpers."""
from unittest.mock import patch, MagicMock

import pytest

from smart_invoice_pro.utils.tenant_service import (
    create_tenant_doc,
    ensure_tenant_exists,
    get_tenant_by_id,
    normalize_partner_code,
)


class TestTenantService:
    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_create_tenant_doc(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        mock_ctr.create_item.return_value = {}

        doc = create_tenant_doc(name="Acme Corp", plan="pro")

        assert doc["name"] == "Acme Corp"
        assert doc["plan"] == "pro"
        assert doc["status"] == "active"
        mock_ctr.create_item.assert_called_once()

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_create_tenant_rejects_duplicate(self, mock_ctr):
        mock_ctr.query_items.return_value = [{"id": "existing"}]
        with pytest.raises(ValueError, match="already exists"):
            create_tenant_doc(name="Dup", tenant_id="existing")

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_ensure_tenant_exists_idempotent(self, mock_ctr):
        existing = {"id": "t1", "name": "Org"}
        mock_ctr.query_items.return_value = [existing]
        assert ensure_tenant_exists("t1") == existing
        mock_ctr.create_item.assert_not_called()

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_ensure_tenant_exists_creates_when_missing(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        mock_ctr.create_item.return_value = {}
        doc = ensure_tenant_exists("t2", name="New Org", owner_user_id="u1")
        assert doc["id"] == "t2"
        assert doc["owner_user_id"] == "u1"


class TestPartnerCode:
    @pytest.mark.parametrize("raw,expected", [
        (None, None),
        ("", None),
        ("   ", None),
        ("ca-sharma", "CA-SHARMA"),
        ("  abc123 ", "ABC123"),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_partner_code(raw) == expected

    @pytest.mark.parametrize("raw", ["ab", "-ABC", "CA SHARMA", "CA_1", "=HYPERLINK", "A" * 33, 123])
    def test_normalize_rejects_malformed(self, raw):
        with pytest.raises(ValueError, match="partner_code"):
            normalize_partner_code(raw)

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_create_tenant_stores_partner_code(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        doc = create_tenant_doc(name="Client Co", partner_code="ca-sharma")
        assert doc["partner_code"] == "CA-SHARMA"
        assert mock_ctr.create_item.call_args.kwargs["body"]["partner_code"] == "CA-SHARMA"

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_create_tenant_without_partner_code_has_no_field(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        doc = create_tenant_doc(name="Client Co")
        assert "partner_code" not in doc

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_existing_tenant_keeps_its_partner_code(self, mock_ctr):
        existing = {"id": "t1", "name": "Org", "partner_code": "FIRST"}
        mock_ctr.query_items.return_value = [existing]
        assert ensure_tenant_exists("t1", partner_code="OTHER")["partner_code"] == "FIRST"
        mock_ctr.create_item.assert_not_called()
        mock_ctr.replace_item.assert_not_called()

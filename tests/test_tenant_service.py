"""Tests for tenant provisioning helpers."""
from unittest.mock import patch, MagicMock

import pytest

from smart_invoice_pro.utils.tenant_service import (
    create_tenant_doc,
    ensure_tenant_exists,
    get_tenant_by_id,
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

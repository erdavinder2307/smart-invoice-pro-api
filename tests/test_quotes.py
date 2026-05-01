"""Tests for quotes API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


SAMPLE_QUOTE = {
    "quote_number": "QT-001",
    "customer_id": "cust-001",
    "customer_name": "Acme Corp",
    "issue_date": "2026-03-01",
    "expiry_date": "2026-04-01",
    "total_amount": 5000.0,
    "status": "Draft",
    "items": [],
}

STORED_QUOTE_A = {
    "id": "qt-aaa-001",
    "quote_number": "QT-001",
    "customer_id": "cust-001",
    "customer_name": "Acme Corp",
    "issue_date": "2026-03-01",
    "expiry_date": "2026-04-01",
    "total_amount": 5000.0,
    "status": "Draft",
    "tenant_id": TENANT_A,
    "items": [],
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreateQuote:
    """POST /api/quotes tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.create_item.return_value = {**SAMPLE_QUOTE, "id": "new-id", "tenant_id": TENANT_A}
            resp = client.post("/api/quotes", json=SAMPLE_QUOTE, headers=headers_a)
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["quote_number"] == "QT-001"

    def test_create_stores_tenant_id(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            client.post("/api/quotes", json=SAMPLE_QUOTE, headers=headers_a)
            call_args = mock_ctr.create_item.call_args
            body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
            assert body["tenant_id"] == TENANT_A

    def test_create_missing_required_fields(self, client, headers_a):
        resp = client.post("/api/quotes", json={}, headers=headers_a)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "details" in data

    def test_create_invalid_status(self, client, headers_a):
        payload = {**SAMPLE_QUOTE, "status": "InvalidStatus"}
        resp = client.post("/api/quotes", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_expiry_before_issue(self, client, headers_a):
        payload = {**SAMPLE_QUOTE, "issue_date": "2026-04-01", "expiry_date": "2026-03-01"}
        resp = client.post("/api/quotes", json=payload, headers=headers_a)
        assert resp.status_code == 400


class TestListQuotes:
    """GET /api/quotes tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.get("/api/quotes", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, list)

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/quotes", headers=headers_a)
            assert resp.status_code == 200
            assert resp.get_json() == []

    def test_list_with_meta_response(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            def _query_side_effect(*args, **kwargs):
                query = kwargs.get("query", "")
                params = kwargs.get("parameters", [])
                if "SELECT * FROM c" in query and "OFFSET" in query:
                    return [STORED_QUOTE_A]
                if "SELECT VALUE COUNT(1) FROM c" in query and "@summary_status" not in query:
                    return [1]
                if "@summary_status" in query:
                    status_value = next((p["value"] for p in params if p["name"] == "@summary_status"), "")
                    return [1] if status_value == "Draft" else [0]
                return []

            mock_ctr.query_items.side_effect = _query_side_effect
            resp = client.get("/api/quotes?include_meta=1&page=1&page_size=10", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, dict)
            assert isinstance(data.get("items"), list)
            assert data.get("total") == 1
            assert data.get("summary", {}).get("Draft") == 1


class TestGetQuote:
    """GET /api/quotes/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.get("/api/quotes/qt-aaa-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/quotes/nonexistent", headers=headers_a)
            assert resp.status_code == 404

    def test_get_cross_tenant_forbidden(self, client, headers_b):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.get("/api/quotes/qt-aaa-001", headers=headers_b)
            assert resp.status_code == 403


class TestUpdateQuote:
    """PUT /api/quotes/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            updated = {**STORED_QUOTE_A, "total_amount": 6000}
            mock_ctr.replace_item.return_value = updated
            resp = client.put("/api/quotes/qt-aaa-001", json={"total_amount": 6000}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/quotes/nope", json={"status": "Sent"}, headers=headers_a)
            assert resp.status_code == 404

    def test_update_cross_tenant_forbidden(self, client, headers_b):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.put("/api/quotes/qt-aaa-001", json={"status": "Sent"}, headers=headers_b)
            assert resp.status_code == 403

    def test_update_invalid_status(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.put("/api/quotes/qt-aaa-001", json={"status": "BadStatus"}, headers=headers_a)
            assert resp.status_code == 400


class TestDeleteQuote:
    """DELETE /api/quotes/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.delete("/api/quotes/qt-aaa-001", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/quotes/nope", headers=headers_a)
            assert resp.status_code == 404

    def test_delete_cross_tenant_forbidden(self, client, headers_b):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.delete("/api/quotes/qt-aaa-001", headers=headers_b)
            assert resp.status_code == 403


class TestNextQuoteNumber:
    """GET /api/quotes/next-number tests."""

    def test_next_number(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/quotes/next-number", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert "quote_number" in data or "next_number" in data


class TestBulkQuotes:
    """POST /api/quotes/bulk tests."""

    def test_bulk_mark_accepted(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            mock_ctr.replace_item.return_value = {**STORED_QUOTE_A, "status": "Accepted"}
            resp = client.post(
                "/api/quotes/bulk",
                json={"action": "mark_accepted", "ids": ["qt-aaa-001"]},
                headers=headers_a,
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success_count"] == 1

    def test_bulk_delete(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_QUOTE_A]
            resp = client.post(
                "/api/quotes/bulk",
                json={"action": "delete", "ids": ["qt-aaa-001"]},
                headers=headers_a,
            )
            assert resp.status_code == 200
            mock_ctr.delete_item.assert_called_once()

    def test_bulk_convert_to_invoice(self, client, headers_a):
        with patch("smart_invoice_pro.api.quotes_api.quotes_container") as mock_quotes, patch(
            "smart_invoice_pro.api.quotes_api.invoices_container"
        ) as mock_invoices:
            mock_quotes.query_items.return_value = [STORED_QUOTE_A]
            mock_invoices.create_item.return_value = {"id": "inv-123", "invoice_number": "INV-123"}

            resp = client.post(
                "/api/quotes/bulk",
                json={"action": "convert_to_invoice", "ids": ["qt-aaa-001"]},
                headers=headers_a,
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success_count"] == 1

    def test_bulk_invalid_payload(self, client, headers_a):
        resp = client.post("/api/quotes/bulk", json={"action": "delete", "ids": []}, headers=headers_a)
        assert resp.status_code == 400

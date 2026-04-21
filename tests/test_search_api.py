from unittest.mock import patch

from tests.conftest import TENANT_A, USER_A


class TestSearchHistoryApi:
    def test_get_history_returns_recent_items(self, client, headers_a):
        with patch("smart_invoice_pro.api.search_api.search_history_container") as mock_container:
            mock_container.query_items.return_value = [
                {
                    "id": "h1",
                    "query": "Acme",
                    "type": "entity",
                    "entity_id": "cust-1",
                    "entity_type": "customer",
                    "created_at": "2025-01-01T00:00:00",
                    "user_id": USER_A,
                    "tenant_id": TENANT_A,
                }
            ]

            response = client.get("/api/search/history?limit=5", headers=headers_a)

            assert response.status_code == 200
            data = response.get_json()
            assert len(data) == 1
            assert data[0]["query"] == "Acme"
            assert data[0]["path"] == "/customers/cust-1"

    def test_post_history_creates_item(self, client, headers_a):
        with patch("smart_invoice_pro.api.search_api.search_history_container") as mock_container:
            response = client.post(
                "/api/search/history",
                json={
                    "query": "INV-101",
                    "type": "entity",
                    "entity_id": "inv-1",
                    "entity_type": "invoice",
                },
                headers=headers_a,
            )

            assert response.status_code == 201
            body = response.get_json()
            assert body["query"] == "INV-101"
            assert body["path"] == "/invoices/edit/inv-1"

            create_payload = mock_container.create_item.call_args.kwargs["body"]
            assert create_payload["tenant_id"] == TENANT_A
            assert create_payload["user_id"] == USER_A

    def test_delete_history_item(self, client, headers_a):
        with patch("smart_invoice_pro.api.search_api.search_history_container") as mock_container:
            mock_container.query_items.return_value = [{"id": "h2", "user_id": USER_A}]

            response = client.delete("/api/search/history/h2", headers=headers_a)

            assert response.status_code == 200
            mock_container.delete_item.assert_called_once_with(item="h2", partition_key=USER_A)

    def test_clear_history(self, client, headers_a):
        with patch("smart_invoice_pro.api.search_api.search_history_container") as mock_container:
            mock_container.query_items.return_value = [
                {"id": "h1", "user_id": USER_A},
                {"id": "h2", "user_id": USER_A},
            ]

            response = client.delete("/api/search/history", headers=headers_a)

            assert response.status_code == 200
            assert mock_container.delete_item.call_count == 2


class TestGlobalSearchApi:
    def test_search_returns_grouped_results(self, client, headers_a):
        with patch("smart_invoice_pro.api.search_api.customers_container") as customers_ctr, patch(
            "smart_invoice_pro.api.search_api.invoices_container"
        ) as invoices_ctr, patch("smart_invoice_pro.api.search_api.products_container") as products_ctr:
            customers_ctr.query_items.return_value = [
                {"id": "cust-1", "display_name": "Acme Corp", "email": "acme@example.com"}
            ]
            invoices_ctr.query_items.return_value = [
                {"id": "inv-1", "invoice_number": "INV-1001", "customer_name": "Acme Corp"}
            ]
            products_ctr.query_items.return_value = [
                {"id": "prod-1", "name": "Acme Widget", "sku": "SKU-1"}
            ]

            response = client.get("/api/search?q=acme", headers=headers_a)

            assert response.status_code == 200
            payload = response.get_json()
            assert payload["query"] == "acme"
            assert len(payload["results"]["customers"]) == 1
            assert len(payload["results"]["invoices"]) == 1
            assert len(payload["results"]["products"]) == 1
            assert payload["results"]["customers"][0]["path"] == "/customers/cust-1"

    def test_search_empty_query_returns_empty_groups(self, client, headers_a):
        response = client.get("/api/search?q=   ", headers=headers_a)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["total"] == 0
        assert payload["results"]["customers"] == []
        assert payload["results"]["invoices"] == []
        assert payload["results"]["products"] == []

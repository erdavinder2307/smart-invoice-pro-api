import datetime
from unittest.mock import patch

import jwt
import pytest

from smart_invoice_pro.app import create_app


def _make_token(user_id="user-1", tenant_id="tenant-1"):
    payload = {
        "id": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, "your_secret_key", algorithm="HS256")


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@patch("smart_invoice_pro.api.product_api.products_container")
def test_requires_token_for_protected_api(mock_products_container, client):
    response = client.get("/api/products")
    assert response.status_code == 401


@patch("smart_invoice_pro.api.product_api.products_container")
def test_forbidden_on_cross_tenant_product_update(mock_products_container, client):
    mock_products_container.query_items.return_value = [
        {"id": "prod-1", "tenant_id": "tenant-2", "is_deleted": False}
    ]

    response = client.put(
        "/api/products/prod-1",
        json={"name": "Renamed"},
        headers={"Authorization": f"Bearer {_make_token(tenant_id='tenant-1')}"},
    )

    assert response.status_code == 403


@patch("smart_invoice_pro.api.product_api.get_container")
@patch("smart_invoice_pro.api.product_api.products_container")
def test_valid_token_allows_tenant_scoped_product_list(mock_products_container, mock_get_container, client):
    mock_products_container.query_items.return_value = [
        {"id": "prod-1", "name": "P1", "tenant_id": "tenant-1", "is_deleted": False}
    ]

    class _MockStockContainer:
        def query_items(self, *args, **kwargs):
            return []

    mock_get_container.return_value = _MockStockContainer()

    response = client.get(
        "/api/products",
        headers={"Authorization": f"Bearer {_make_token(tenant_id='tenant-1')}"},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "P1"

import pytest
import json
import jwt
import datetime
from unittest.mock import patch, MagicMock
from smart_invoice_pro.app import create_app


def _auth_headers(tenant_id="tenant-1", user_id="user-1"):
    token = jwt.encode(
        {
            "id": user_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        },
        "your_secret_key",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@patch('smart_invoice_pro.api.product_api.products_container')
def test_create_product_success(mock_container, client):
    # Mock that no product exists with the same name
    mock_container.query_items.return_value = []
    
    payload = {
        'name': 'Test New Item',
        'price': 100,
        'purchase_rate': 80,
        'unit': 'Nos',
        'sales_enabled': True,
        'purchase_enabled': True
    }
    
    response = client.post('/api/products', json=payload, headers=_auth_headers())
    assert response.status_code == 201
    data = json.loads(response.data)
    assert data['name'] == 'Test New Item'
    assert data['is_deleted'] is False
    assert mock_container.create_item.called

@patch('smart_invoice_pro.api.product_api.products_container')
def test_create_product_duplicate_name(mock_container, client):
    # Mock that a product already exists
    mock_container.query_items.return_value = [{'id': 'existing-id'}]
    
    payload = {
        'name': 'Existing Item',
        'price': 100,
        'unit': 'Nos'
    }
    
    response = client.post('/api/products', json=payload, headers=_auth_headers())
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['type'] == 'business_error'
    assert 'already exists' in data['error']['message']
    assert 'name' in data['error']['fields']

@patch('smart_invoice_pro.api.product_api.products_container')
def test_create_product_validation_error(mock_container, client):
    payload = {
        'name': '', # empty name
        'price': -10, # negative price
        'unit': 'Nos',
        'sales_enabled': True
    }
    
    response = client.post('/api/products', json=payload, headers=_auth_headers())
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['type'] == 'validation_error'
    assert 'name' in data['error']['fields']

@patch('smart_invoice_pro.api.product_api._item_used_in_invoices')
@patch('smart_invoice_pro.api.product_api.products_container')
def test_delete_product_soft_delete(mock_container, mock_invoices, client):
    # Mock existing item
    mock_item = {'id': 'test-id', 'name': 'Test Item', 'is_deleted': False, 'tenant_id': 'tenant-1'}
    mock_container.query_items.return_value = [mock_item]
    mock_invoices.return_value = 0 # Not used in invoices
    
    response = client.delete('/api/products/test-id', headers=_auth_headers())
    assert response.status_code == 200
    
    # Verify replace_item was called with is_deleted=True
    mock_container.replace_item.assert_called_once()
    args, kwargs = mock_container.replace_item.call_args
    assert kwargs['body']['is_deleted'] is True
    assert kwargs['body']['deleted_at'] is not None

@patch('smart_invoice_pro.api.product_api._item_used_in_invoices')
@patch('smart_invoice_pro.api.product_api.products_container')
def test_delete_product_used_in_invoice(mock_container, mock_invoices, client):
    # Mock existing item
    mock_item = {'id': 'test-id', 'name': 'Test Item', 'is_deleted': False, 'tenant_id': 'tenant-1'}
    mock_container.query_items.return_value = [mock_item]
    mock_invoices.return_value = 2 # Used in 2 invoices
    
    response = client.delete('/api/products/test-id', headers=_auth_headers())
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['message'] == 'Product archived'
    assert isinstance(data.get('dependencySummary'), dict)
    
@patch('smart_invoice_pro.api.product_api.get_container')
@patch('smart_invoice_pro.api.product_api.products_container')
def test_list_products_excludes_soft_deleted(mock_container, mock_get_container, client):
    mock_items = [
        {'id': '1', 'name': 'Active', 'is_deleted': False},
        {'id': '2', 'name': 'Deleted', 'is_deleted': True}
    ]
    mock_container.query_items.return_value = mock_items
    
    # Mock stock container
    mock_stock_container = MagicMock()
    mock_stock_container.read_all_items.return_value = []
    mock_get_container.return_value = mock_stock_container
    
    response = client.get('/api/products', headers=_auth_headers())
    assert response.status_code == 200
    data = json.loads(response.data)
    
    assert len(data) == 1
    assert data[0]['name'] == 'Active'

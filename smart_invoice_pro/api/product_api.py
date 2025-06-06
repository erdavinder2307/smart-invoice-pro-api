from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container
import uuid
from flasgger import swag_from
from datetime import datetime
from fastapi import APIRouter

# Create or get the products container (partition key: /product_id)
products_container = get_container("products", "/product_id")

product_blueprint = Blueprint('products', __name__)
stock_summary_router = APIRouter()

@product_blueprint.route('/products', methods=['POST'])
@swag_from({
    'tags': ['Products'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'category': {'type': 'string'},
                    'price': {'type': 'number'},
                    'tax_rate': {'type': 'number'},
                    'unit': {'type': 'string'}
                },
                'required': ['name', 'price', 'unit']
            },
            'description': 'Product data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Product created',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'product_id': 'uuid',
                    'name': 'Product A',
                    'description': 'A sample product',
                    'category': 'Category 1',
                    'price': 100.0,
                    'tax_rate': 18.0,
                    'unit': 'pcs',
                    'created_at': '2025-06-06T12:00:00Z',
                    'updated_at': '2025-06-06T12:00:00Z'
                }
            }
        }
    }
})
def create_product():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'product_id': str(uuid.uuid4()),
        'name': data['name'],
        'description': data.get('description', ''),
        'category': data.get('category', ''),
        'price': data['price'],
        'tax_rate': data.get('tax_rate', 0.0),
        'unit': data['unit'],
        'created_at': now,
        'updated_at': now
    }
    products_container.create_item(body=item)
    return jsonify(item), 201

@product_blueprint.route('/products', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'List of all products',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'product_id': 'uuid',
                        'name': 'Product A',
                        'description': 'A sample product',
                        'category': 'Category 1',
                        'price': 100.0,
                        'tax_rate': 18.0,
                        'unit': 'pcs',
                        'created_at': '2025-06-06T12:00:00Z',
                        'updated_at': '2025-06-06T12:00:00Z'
                    }
                ]
            }
        }
    }
})
def list_products():
    items = list(products_container.read_all_items())
    return jsonify(items)

@product_blueprint.route('/products/<product_id>', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'parameters': [
        {
            'name': 'product_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Product ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Product details',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'product_id': 'uuid',
                    'name': 'Product A',
                    'description': 'A sample product',
                    'category': 'Category 1',
                    'price': 100.0,
                    'tax_rate': 18.0,
                    'unit': 'pcs',
                    'created_at': '2025-06-06T12:00:00Z',
                    'updated_at': '2025-06-06T12:00:00Z'
                }
            }
        },
        '404': {
            'description': 'Product not found',
            'examples': {'application/json': {'error': 'Product not found'}}
        }
    }
})
def get_product(product_id):
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    return jsonify(items[0])

@product_blueprint.route('/products/<product_id>', methods=['PUT'])
@swag_from({
    'tags': ['Products'],
    'parameters': [
        {
            'name': 'product_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Product ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'category': {'type': 'string'},
                    'price': {'type': 'number'},
                    'tax_rate': {'type': 'number'},
                    'unit': {'type': 'string'}
                }
            },
            'description': 'Product data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Product updated',
            'examples': {'application/json': {'id': 'uuid', 'product_id': 'uuid', 'name': 'Product A', 'price': 120.0}}
        },
        '404': {
            'description': 'Product not found',
            'examples': {'application/json': {'error': 'Product not found'}}
        }
    }
})
def update_product(product_id):
    data = request.get_json()
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    item = items[0]
    for field in ['name', 'description', 'category', 'price', 'tax_rate', 'unit']:
        if field in data:
            item[field] = data[field]
    item['updated_at'] = datetime.utcnow().isoformat()
    products_container.replace_item(item=item['id'], body=item)
    return jsonify(item)

@product_blueprint.route('/products/<product_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Products'],
    'parameters': [
        {
            'name': 'product_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Product ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Product deleted',
            'examples': {'application/json': {'message': 'Product deleted'}}
        },
        '404': {
            'description': 'Product not found',
            'examples': {'application/json': {'error': 'Product not found'}}
        }
    }
})
def delete_product(product_id):
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    item = items[0]
    products_container.delete_item(item=item['id'], partition_key=item['product_id'])
    return jsonify({'message': 'Product deleted'})

@product_blueprint.route('/products/stock-summary', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'Stock summary for all products',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'name': 'Product A',
                        'sku': 'SKU123',
                        'stock': 100.0
                    }
                ]
            }
        }
    }
})
def products_stock_summary():
    products = list(products_container.read_all_items())
    stock_transactions = list(get_container("stock", "/product_id").read_all_items())
    # Aggregate stock by product_id
    stock_map = {}
    for txn in stock_transactions:
        pid = txn.get('product_id')
        qty = float(txn.get('quantity', 0))
        if pid not in stock_map:
            stock_map[pid] = 0.0
        if txn.get('type') == 'IN':
            stock_map[pid] += qty
        elif txn.get('type') == 'OUT':
            stock_map[pid] -= qty
    result = []
    for product in products:
        pid = product.get('id')
        result.append({
            'id': pid,
            'name': product.get('name', ''),
            'sku': product.get('sku', ''),
            'stock': stock_map.get(pid, 0.0)
        })
    return jsonify(result)

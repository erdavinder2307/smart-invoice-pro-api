from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container
from flasgger import swag_from
from datetime import datetime
import uuid

# Create or get the stock container (partition key: /product_id)
stock_container = get_container("stock", "/product_id")

stock_blueprint = Blueprint('stock', __name__)

@stock_blueprint.route('/stock/add', methods=['POST'])
@swag_from({
    'tags': ['Stock'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'product_id': {'type': 'string'},
                    'quantity': {'type': 'number'},
                    'source': {'type': 'string'}
                },
                'required': ['product_id', 'quantity']
            },
            'description': 'Stock addition (purchase) data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Stock added',
            'examples': {'application/json': {'message': 'Stock added', 'transaction': {}}}
        }
    }
})
def add_stock():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    transaction = {
        'id': str(uuid.uuid4()),
        'product_id': data['product_id'],
        'quantity': float(data['quantity']),
        'type': 'IN',
        'source': data.get('source', 'Purchase'),
        'timestamp': now
    }
    stock_container.create_item(body=transaction)
    return jsonify({'message': 'Stock added', 'transaction': transaction}), 201

@stock_blueprint.route('/stock/reduce', methods=['POST'])
@swag_from({
    'tags': ['Stock'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'product_id': {'type': 'string'},
                    'quantity': {'type': 'number'},
                    'source': {'type': 'string'}
                },
                'required': ['product_id', 'quantity']
            },
            'description': 'Stock reduction (sale) data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Stock reduced',
            'examples': {'application/json': {'message': 'Stock reduced', 'transaction': {}}}
        }
    }
})
def reduce_stock():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    transaction = {
        'id': str(uuid.uuid4()),
        'product_id': data['product_id'],
        'quantity': float(data['quantity']),
        'type': 'OUT',
        'source': data.get('source', 'Sale'),
        'timestamp': now
    }
    stock_container.create_item(body=transaction)
    return jsonify({'message': 'Stock reduced', 'transaction': transaction}), 201

@stock_blueprint.route('/stock/<product_id>', methods=['GET'])
@swag_from({
    'tags': ['Stock'],
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
            'description': 'Current stock for product',
            'examples': {'application/json': {'product_id': 'uuid', 'current_stock': 100}}
        }
    }
})
def get_current_stock(product_id):
    query = f"SELECT c.type, c.quantity FROM c WHERE c.product_id = '{product_id}'"
    items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
    stock_in = sum(item['quantity'] for item in items if item['type'] == 'IN')
    stock_out = sum(item['quantity'] for item in items if item['type'] == 'OUT')
    current_stock = stock_in - stock_out
    return jsonify({'product_id': product_id, 'current_stock': current_stock})

@stock_blueprint.route('/stock/ledger/<product_id>', methods=['GET'])
@swag_from({
    'tags': ['Stock'],
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
            'description': 'Stock transaction history',
            'examples': {'application/json': [{'id': 'uuid', 'product_id': 'uuid', 'quantity': 10, 'type': 'IN', 'source': 'Purchase', 'timestamp': '2025-06-06T12:00:00Z'}]}
        }
    }
})
def get_stock_ledger(product_id):
    query = f"SELECT * FROM c WHERE c.product_id = '{product_id}' ORDER BY c.timestamp ASC"
    items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
    return jsonify(items)

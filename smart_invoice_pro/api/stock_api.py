from flask import Blueprint, request, jsonify, current_app
from smart_invoice_pro.utils.cosmos_client import stock_container, products_container
from flasgger import swag_from
from datetime import datetime
import uuid

stock_blueprint = Blueprint('stock', __name__)


def _parse_positive_quantity(data):
    try:
        quantity = float(data.get('quantity', 0))
    except (TypeError, ValueError):
        return None, jsonify({'error': 'quantity must be a number'}), 400
    if quantity <= 0:
        return None, jsonify({'error': 'quantity must be greater than 0'}), 400
    return quantity, None, None


def _product_exists_for_tenant(product_id, tenant_id):
    items = list(products_container.query_items(
        query=(
            "SELECT TOP 1 c.id, c.is_deleted FROM c "
            "WHERE c.id = @id AND c.tenant_id = @tenant_id"
        ),
        parameters=[
            {"name": "@id", "value": product_id},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    return bool(items) and not items[0].get('is_deleted', False)


def _compute_current_stock(product_id, tenant_id):
    items = list(stock_container.query_items(
        query=(
            "SELECT c.type, c.quantity FROM c "
            "WHERE c.product_id = @product_id AND c.tenant_id = @tenant_id"
        ),
        parameters=[
            {"name": "@product_id", "value": product_id},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    stock_in = sum(float(item.get('quantity', 0)) for item in items if item.get('type') == 'IN')
    stock_out = sum(float(item.get('quantity', 0)) for item in items if item.get('type') == 'OUT')
    return stock_in - stock_out

# Add a test route to verify the blueprint is working
@stock_blueprint.route('/stock/test', methods=['GET'])
def test_stock_api():
    return jsonify({'message': 'Stock API is working!', 'timestamp': datetime.utcnow().isoformat()})

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
    data = request.get_json() or {}
    current_app.logger.info("stock.add payload=%s tenant_id=%s", data, getattr(request, 'tenant_id', None))
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400
    quantity, err_resp, status = _parse_positive_quantity(data)
    if err_resp is not None:
        return err_resp, status
    if not _product_exists_for_tenant(product_id, request.tenant_id):
        return jsonify({'error': 'Product not found'}), 404

    now = datetime.utcnow().isoformat()
    transaction = {
        'id': str(uuid.uuid4()),
        'product_id': product_id,
        'quantity': quantity,
        'type': 'IN',
        'source': data.get('source', 'Purchase'),
        'timestamp': now,
        'tenant_id': request.tenant_id,
        'user_id': getattr(request, 'user_id', None),
    }
    stock_container.create_item(body=transaction)
    current_stock = _compute_current_stock(product_id, request.tenant_id)
    response = {
        'message': 'Stock added',
        'transaction': transaction,
        'current_stock': current_stock,
        'operation': 'increase',
    }
    current_app.logger.info("stock.add success product_id=%s current_stock=%s", product_id, current_stock)
    return jsonify(response), 201

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
    data = request.get_json() or {}
    current_app.logger.info("stock.reduce payload=%s tenant_id=%s", data, getattr(request, 'tenant_id', None))
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400
    quantity, err_resp, status = _parse_positive_quantity(data)
    if err_resp is not None:
        return err_resp, status
    if not _product_exists_for_tenant(product_id, request.tenant_id):
        return jsonify({'error': 'Product not found'}), 404

    now = datetime.utcnow().isoformat()
    transaction = {
        'id': str(uuid.uuid4()),
        'product_id': product_id,
        'quantity': quantity,
        'type': 'OUT',
        'source': data.get('source', 'Sale'),
        'timestamp': now,
        'tenant_id': request.tenant_id,
        'user_id': getattr(request, 'user_id', None),
    }
    stock_container.create_item(body=transaction)
    current_stock = _compute_current_stock(product_id, request.tenant_id)
    response = {
        'message': 'Stock reduced',
        'transaction': transaction,
        'current_stock': current_stock,
        'operation': 'decrease',
    }
    current_app.logger.info("stock.reduce success product_id=%s current_stock=%s", product_id, current_stock)
    return jsonify(response), 201

@stock_blueprint.route('/stock/<product_id>', methods=['GET', 'OPTIONS'])
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
        },
        '500': {
            'description': 'Internal server error',
            'examples': {'application/json': {'error': 'Database error'}}
        }
    }
})
def get_current_stock(product_id):
    current_app.logger.info("stock.current request method=%s product_id=%s tenant_id=%s", request.method, product_id, getattr(request, 'tenant_id', None))
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        items = list(stock_container.query_items(
            query=(
                "SELECT c.type, c.quantity FROM c "
                "WHERE c.product_id = @product_id AND c.tenant_id = @tenant_id"
            ),
            parameters=[
                {"name": "@product_id", "value": product_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        stock_in = sum(float(item['quantity']) for item in items if item['type'] == 'IN')
        stock_out = sum(float(item['quantity']) for item in items if item['type'] == 'OUT')
        current_stock = stock_in - stock_out
        
        return jsonify({
            'product_id': product_id, 
            'current_stock': current_stock,
            'stock_in': stock_in,
            'stock_out': stock_out
        })
        
    except Exception as e:
        current_app.logger.exception("stock.current failed product_id=%s", product_id)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@stock_blueprint.route('/stock/ledger/<product_id>', methods=['GET', 'OPTIONS'])
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
        },
        '500': {
            'description': 'Internal server error',
            'examples': {'application/json': {'error': 'Database error'}}
        }
    }
})
def get_stock_ledger(product_id):
    current_app.logger.info("stock.ledger request method=%s product_id=%s tenant_id=%s", request.method, product_id, getattr(request, 'tenant_id', None))
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        items = list(stock_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.product_id = @product_id "
                "AND c.tenant_id = @tenant_id ORDER BY c.timestamp ASC"
            ),
            parameters=[
                {"name": "@product_id", "value": product_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        # Calculate running balance for each transaction
        running_balance = 0
        ledger_with_balance = []
        
        for item in items:
            try:
                quantity = float(item.get('quantity', 0))
                if item.get('type') == 'IN':
                    running_balance += quantity
                else:
                    running_balance -= quantity
                
                ledger_entry = {
                    **item,
                    'balance': max(0, running_balance),  # Ensure non-negative balance
                    'date': item.get('timestamp', item.get('date', datetime.utcnow().isoformat())),
                    'quantity': quantity
                }
                ledger_with_balance.append(ledger_entry)
            except (ValueError, TypeError):
                continue
        
        return jsonify(ledger_with_balance)
        
    except Exception as e:
        current_app.logger.exception("stock.ledger failed product_id=%s", product_id)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@stock_blueprint.route('/stock/adjust', methods=['POST', 'OPTIONS'])
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
                    'type': {'type': 'string'},
                    'quantity': {'type': 'number'},
                    'reason': {'type': 'string'},
                    'reference_number': {'type': 'string'},
                    'unit_cost': {'type': 'number'},
                    'location': {'type': 'string'},
                    'adjustment_date': {'type': 'string'},
                    'user_id': {'type': 'string'}
                },
                'required': ['product_id', 'type', 'quantity', 'reason']
            },
            'description': 'Stock adjustment data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Stock adjustment processed',
            'examples': {'application/json': {'message': 'Stock adjustment processed successfully', 'adjustment': {}}}
        },
        '400': {
            'description': 'Bad request - validation error',
            'examples': {'application/json': {'error': 'Missing required fields'}}
        },
        '500': {
            'description': 'Internal server error',
            'examples': {'application/json': {'error': 'Database error'}}
        }
    }
})
def adjust_stock():
    current_app.logger.info("stock.adjust request method=%s tenant_id=%s", request.method, getattr(request, 'tenant_id', None))
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json() or {}
        current_app.logger.info("stock.adjust payload=%s", data)
        
        # Validate required fields
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        required_fields = ['product_id', 'type', 'quantity', 'reason']
        for field in required_fields:
            if field not in data or data[field] is None:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate quantity
        try:
            quantity = float(data['quantity'])
            if quantity == 0:
                return jsonify({'error': 'Quantity cannot be zero'}), 400
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid quantity format'}), 400
        
        now = datetime.utcnow().isoformat()
        
        adjustment = {
            'id': str(uuid.uuid4()),
            'product_id': str(data['product_id']),
            'quantity': quantity,
            'type': 'IN' if quantity > 0 else 'OUT',
            'adjustment_type': str(data['type']),
            'reason': str(data['reason']),
            'reference_number': str(data.get('reference_number', '')) if data.get('reference_number') else None,
            'unit_cost': float(data['unit_cost']) if data.get('unit_cost') else None,
            'location': str(data.get('location', 'Main Warehouse')),
            'adjustment_date': str(data.get('adjustment_date', now.split('T')[0])),
            'user_id': str(data.get('user_id', 'system')),
            'timestamp': now,
            'source': f"{data['type']} Adjustment",
            'tenant_id': request.tenant_id,
        }
        
        # Create the stock adjustment record
        stock_container.create_item(body=adjustment)
        
        current_stock = _compute_current_stock(str(data['product_id']), request.tenant_id)
        response = {
            'message': 'Stock adjustment processed successfully', 
            'adjustment': adjustment,
            'current_stock': current_stock,
        }
        current_app.logger.info("stock.adjust success product_id=%s current_stock=%s", data['product_id'], current_stock)
        return jsonify(response), 201
        
    except Exception as e:
        current_app.logger.exception("stock.adjust failed")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@stock_blueprint.route('/stock/recent-adjustments', methods=['GET', 'OPTIONS'])
@swag_from({
    'tags': ['Stock'],
    'responses': {
        '200': {
            'description': 'Recent stock adjustments',
            'examples': {'application/json': [{'id': 'uuid', 'product_id': 'uuid', 'quantity': 10, 'type': 'IN', 'adjustment_type': 'PURCHASE', 'timestamp': '2025-06-06T12:00:00Z'}]}
        },
        '500': {
            'description': 'Internal server error',
            'examples': {'application/json': {'error': 'Database error'}}
        }
    }
})
def get_recent_adjustments():
    current_app.logger.info("stock.recent-adjustments request method=%s tenant_id=%s", request.method, getattr(request, 'tenant_id', None))
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get recent adjustments with proper ordering
        items = list(stock_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.tenant_id = @tenant_id "
                "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 50"
            ),
            parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
            enable_cross_partition_query=True
        ))
        
        # Process and clean up the data
        processed_items = []
        for item in items:
            try:
                processed_item = {
                    **item,
                    'quantity': float(item.get('quantity', 0)),
                    'date': item.get('timestamp', item.get('date', datetime.utcnow().isoformat()))
                }
                processed_items.append(processed_item)
            except (ValueError, TypeError):
                continue  # Skip invalid entries
        
        return jsonify(processed_items)
        
    except Exception as e:
        current_app.logger.exception("stock.recent-adjustments failed")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

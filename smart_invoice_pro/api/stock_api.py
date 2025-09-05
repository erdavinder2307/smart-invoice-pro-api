from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container
from flasgger import swag_from
from datetime import datetime
import uuid

# Create or get the stock container (partition key: /product_id)
stock_container = get_container("stock", "/product_id")

stock_blueprint = Blueprint('stock', __name__)

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
    print(f"Received request: {request.method} to /stock/{product_id}")  # Debug log
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        query = f"SELECT c.type, c.quantity FROM c WHERE c.product_id = '{product_id}'"
        items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
        
        stock_in = sum(float(item['quantity']) for item in items if item['type'] == 'IN')
        stock_out = sum(float(item['quantity']) for item in items if item['type'] == 'OUT')
        current_stock = stock_in - stock_out
        
        return jsonify({
            'product_id': product_id, 
            'current_stock': max(0, current_stock),  # Ensure non-negative stock
            'stock_in': stock_in,
            'stock_out': stock_out
        })
        
    except Exception as e:
        print(f"Error in get_current_stock: {str(e)}")  # For debugging
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
    print(f"Received request: {request.method} to /stock/ledger/{product_id}")  # Debug log
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        query = f"SELECT * FROM c WHERE c.product_id = '{product_id}' ORDER BY c.timestamp ASC"
        items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
        
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
            except (ValueError, TypeError) as e:
                print(f"Error processing ledger entry: {str(e)}")  # Skip invalid entries
                continue
        
        return jsonify(ledger_with_balance)
        
    except Exception as e:
        print(f"Error in get_stock_ledger: {str(e)}")  # For debugging
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
    print(f"Received request: {request.method} to /stock/adjust")  # Debug log
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json()
        
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
            'source': f"{data['type']} Adjustment"
        }
        
        # Create the stock adjustment record
        stock_container.create_item(body=adjustment)
        
        return jsonify({
            'message': 'Stock adjustment processed successfully', 
            'adjustment': adjustment
        }), 201
        
    except Exception as e:
        print(f"Error in adjust_stock: {str(e)}")  # For debugging
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
    print(f"Received request: {request.method} to /stock/recent-adjustments")  # Debug log
    
    # Handle OPTIONS request for CORS
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get recent adjustments with proper ordering
        query = "SELECT * FROM c ORDER BY c.timestamp DESC OFFSET 0 LIMIT 50"
        items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
        
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
        print(f"Error in get_recent_adjustments: {str(e)}")  # For debugging
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

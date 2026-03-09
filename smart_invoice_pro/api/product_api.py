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
                    'unit': {'type': 'string'},
                    'reorder_level': {'type': 'number'},
                    'reorder_qty': {'type': 'number'},
                    'preferred_vendor_id': {'type': 'string'}
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
        'reorder_level': data.get('reorder_level', 0),
        'reorder_qty': data.get('reorder_qty', 0),
        'preferred_vendor_id': data.get('preferred_vendor_id', ''),
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
                        'stock': 100.0,
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
    for product in items:
        pid = product.get('id')
        product_with_stock = dict(product)
        product_with_stock['stock'] = stock_map.get(pid, 0.0)
        result.append(product_with_stock)
    return jsonify(result)

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
                    'unit': {'type': 'string'},
                    'reorder_level': {'type': 'number'},
                    'reorder_qty': {'type': 'number'},
                    'preferred_vendor_id': {'type': 'string'}
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
    for field in ['name', 'description', 'category', 'price', 'tax_rate', 'unit', 'reorder_level', 'reorder_qty', 'preferred_vendor_id']:
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

@product_blueprint.route('/products/low-stock', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'Products with low stock (at or below reorder level)',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'name': 'Product A',
                        'current_stock': 5.0,
                        'reorder_level': 10.0,
                        'reorder_qty': 50.0,
                        'preferred_vendor_id': 'vendor-uuid'
                    }
                ]
            }
        }
    }
})
def get_low_stock_products():
    """Get all products where current stock is at or below reorder level"""
    products = list(products_container.read_all_items())
    stock_transactions = list(get_container("stock", "/product_id").read_all_items())
    
    # Calculate current stock for each product
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
    
    # Filter products with low stock
    low_stock_products = []
    for product in products:
        pid = product.get('id')
        current_stock = stock_map.get(pid, 0.0)
        reorder_level = float(product.get('reorder_level', 0))
        
        # Only include if reorder_level is set and current stock is at or below it
        if reorder_level > 0 and current_stock <= reorder_level:
            low_stock_products.append({
                'id': pid,
                'product_id': product.get('product_id', pid),
                'name': product.get('name', ''),
                'category': product.get('category', ''),
                'unit': product.get('unit', ''),
                'current_stock': current_stock,
                'reorder_level': reorder_level,
                'reorder_qty': float(product.get('reorder_qty', 0)),
                'preferred_vendor_id': product.get('preferred_vendor_id', ''),
                'price': product.get('price', 0)
            })
    
    return jsonify(low_stock_products)

@product_blueprint.route('/products/<product_id>/restock', methods=['POST'])
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
            'required': False,
            'schema': {
                'type': 'object',
                'properties': {
                    'quantity': {'type': 'number', 'description': 'Override reorder quantity'},
                    'vendor_id': {'type': 'string', 'description': 'Override preferred vendor'}
                }
            }
        }
    ],
    'responses': {
        '201': {
            'description': 'Purchase order created for restocking',
            'examples': {
                'application/json': {
                    'message': 'Purchase order created',
                    'po_id': 'uuid',
                    'po_number': 'PO-001',
                    'vendor_id': 'vendor-uuid',
                    'items': [{'product_id': 'uuid', 'quantity': 50}]
                }
            }
        },
        '404': {
            'description': 'Product not found'
        },
        '400': {
            'description': 'Invalid request - missing vendor or reorder quantity'
        }
    }
})
def create_restock_po(product_id):
    """Create a purchase order to restock a product"""
    data = request.get_json() or {}
    
    # Get product details
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    products = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not products:
        return jsonify({'error': 'Product not found'}), 404
    
    product = products[0]
    
    # Determine vendor and quantity
    vendor_id = data.get('vendor_id') or product.get('preferred_vendor_id')
    if not vendor_id:
        return jsonify({'error': 'No vendor specified and no preferred vendor set for product'}), 400
    
    quantity = data.get('quantity') or product.get('reorder_qty', 0)
    if quantity <= 0:
        return jsonify({'error': 'Invalid reorder quantity'}), 400
    
    # Get next PO number
    po_container = get_container("purchase_orders", "/vendor_id")
    all_pos = list(po_container.read_all_items())
    next_number = len(all_pos) + 1
    po_number = f"PO-{next_number:03d}"
    
    # Calculate amounts
    unit_price = float(product.get('price', 0))
    subtotal = unit_price * quantity
    tax_rate = float(product.get('tax_rate', 0))
    tax_amount = subtotal * (tax_rate / 100)
    total = subtotal + tax_amount
    
    now = datetime.utcnow().isoformat()
    
    # Create PO
    po = {
        'id': str(uuid.uuid4()),
        'po_number': po_number,
        'vendor_id': vendor_id,
        'order_date': now.split('T')[0],
        'delivery_date': '',
        'subtotal': subtotal,
        'cgst_amount': tax_amount / 2 if tax_rate > 0 else 0,
        'sgst_amount': tax_amount / 2 if tax_rate > 0 else 0,
        'igst_amount': 0,
        'total_tax': tax_amount,
        'total_amount': total,
        'status': 'Draft',
        'notes': f'Auto-generated restock order for {product.get("name")}',
        'subject': f'Restock - {product.get("name")}',
        'items': [{
            'item_name': product.get('name', ''),
            'product_id': product_id,
            'quantity': quantity,
            'rate': unit_price,
            'tax': tax_rate,
            'amount': subtotal
        }],
        'created_at': now,
        'updated_at': now,
        'auto_generated': True
    }
    
    po_container.create_item(body=po)
    
    return jsonify({
        'message': 'Purchase order created successfully',
        'po_id': po['id'],
        'po_number': po_number,
        'vendor_id': vendor_id,
        'product_id': product_id,
        'quantity': quantity,
        'total_amount': total
    }), 201


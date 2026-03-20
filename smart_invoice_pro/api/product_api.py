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

# ─────────────────────────────────────────────
#  Validation helpers
# ─────────────────────────────────────────────
MAX_NAME_LEN = 255
MAX_DESC_LEN = 1000
MAX_PRICE = 99_999_999


def _validate_product_fields(data, is_update=False):
    """
    Validates common product fields.
    Returns (errors_list, cleaned_data) – errors_list is [] on success.
    """
    errors = []

    name = data.get('name', '')
    if not is_update or 'name' in data:
        if not str(name).strip():
            errors.append('Item name is required')
        elif len(str(name)) > MAX_NAME_LEN:
            errors.append(f'Item name must be {MAX_NAME_LEN} characters or fewer')

    for field_label, field_key in [('Selling price', 'price'), ('Cost price', 'purchase_rate')]:
        if field_key in data:
            val = data.get(field_key)
            try:
                val = float(val)
                if val < 0:
                    errors.append(f'{field_label} cannot be negative')
                elif val > MAX_PRICE:
                    errors.append(f'{field_label} cannot exceed ₹{MAX_PRICE:,}')
            except (TypeError, ValueError):
                errors.append(f'{field_label} must be a number')

    for desc_label, desc_key in [('Description', 'description'), ('Purchase description', 'purchase_description')]:
        if desc_key in data:
            if len(str(data.get(desc_key, ''))) > MAX_DESC_LEN:
                errors.append(f'{desc_label} must be {MAX_DESC_LEN} characters or fewer')

    return errors


def _name_exists(name, exclude_id=None):
    """
    Check if an item with the same name (case-insensitive) already exists
    and is not soft-deleted.
    """
    name_lower = name.strip().lower()
    query = (
        "SELECT c.id FROM c "
        "WHERE LOWER(c.name) = @name "
        "AND (NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false)"
    )
    params = [{"name": "@name", "value": name_lower}]
    items = list(products_container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True
    ))
    if exclude_id:
        items = [i for i in items if i.get('id') != exclude_id]
    return len(items) > 0


def _item_used_in_invoices(product_id):
    """
    Check if a product is referenced in any invoice line item.
    Returns the count of invoices containing this product.
    """
    try:
        invoices_container = get_container("invoices")
        query = (
            "SELECT VALUE COUNT(1) FROM c "
            "JOIN item IN c.items "
            "WHERE item.product_id = @pid"
        )
        params = [{"name": "@pid", "value": product_id}]
        result = list(invoices_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        return result[0] if result else 0
    except Exception:
        return 0


# ─────────────────────────────────────────────
#  CREATE
# ─────────────────────────────────────────────
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
                    'item_type': {'type': 'string'},
                    'name': {'type': 'string'},
                    'hsn_sac': {'type': 'string'},
                    'tax_preference': {'type': 'string'},
                    'description': {'type': 'string'},
                    'purchase_description': {'type': 'string'},
                    'category': {'type': 'string'},
                    'price': {'type': 'number'},
                    'purchase_rate': {'type': 'number'},
                    'tax_rate': {'type': 'number'},
                    'unit': {'type': 'string'},
                    'sales_enabled': {'type': 'boolean'},
                    'purchase_enabled': {'type': 'boolean'},
                    'sales_account': {'type': 'string'},
                    'purchase_account': {'type': 'string'},
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
        },
        '400': {
            'description': 'Validation error or duplicate name',
        }
    }
})
def create_product():
    data = request.get_json()

    # Field-level validation
    errors = _validate_product_fields(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 400

    # Duplicate name check
    name = str(data.get('name', '')).strip()
    if _name_exists(name):
        return jsonify({
            'error': 'An item with this name already exists',
            'field': 'name'
        }), 400

    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'product_id': str(uuid.uuid4()),
        'item_type': data.get('item_type', 'goods'),
        'name': name,
        'hsn_sac': data.get('hsn_sac', ''),
        'tax_preference': data.get('tax_preference', 'taxable'),
        'description': data.get('description', ''),
        'purchase_description': data.get('purchase_description', ''),
        'category': data.get('category', ''),
        'price': float(data.get('price', 0)),
        'purchase_rate': float(data.get('purchase_rate', 0.0)),
        'tax_rate': data.get('tax_rate', 0.0),
        'unit': data.get('unit', ''),
        'sales_enabled': data.get('sales_enabled', True),
        'purchase_enabled': data.get('purchase_enabled', True),
        'sales_account': data.get('sales_account', 'Sales'),
        'purchase_account': data.get('purchase_account', 'Cost of Goods Sold'),
        'reorder_level': data.get('reorder_level', 0),
        'reorder_qty': data.get('reorder_qty', 0),
        'preferred_vendor_id': data.get('preferred_vendor_id', ''),
        'is_deleted': False,
        'deleted_at': None,
        'created_at': now,
        'updated_at': now
    }
    products_container.create_item(body=item)
    return jsonify(item), 201


# ─────────────────────────────────────────────
#  LIST
# ─────────────────────────────────────────────
@product_blueprint.route('/products', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'List of all products (excluding soft-deleted)',
        }
    }
})
def list_products():
    items = list(products_container.read_all_items())
    # Exclude soft-deleted items
    items = [p for p in items if not p.get('is_deleted', False)]

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


# ─────────────────────────────────────────────
#  GET ONE
# ─────────────────────────────────────────────
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
        '200': {'description': 'Product details'},
        '404': {'description': 'Product not found'}
    }
})
def get_product(product_id):
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    product = items[0]
    if product.get('is_deleted', False):
        return jsonify({'error': 'Product not found'}), 404
    return jsonify(product)


# ─────────────────────────────────────────────
#  UPDATE
# ─────────────────────────────────────────────
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
                    'item_type': {'type': 'string'},
                    'name': {'type': 'string'},
                    'hsn_sac': {'type': 'string'},
                    'tax_preference': {'type': 'string'},
                    'description': {'type': 'string'},
                    'purchase_description': {'type': 'string'},
                    'category': {'type': 'string'},
                    'price': {'type': 'number'},
                    'purchase_rate': {'type': 'number'},
                    'tax_rate': {'type': 'number'},
                    'unit': {'type': 'string'},
                    'sales_enabled': {'type': 'boolean'},
                    'purchase_enabled': {'type': 'boolean'},
                    'sales_account': {'type': 'string'},
                    'purchase_account': {'type': 'string'},
                    'reorder_level': {'type': 'number'},
                    'reorder_qty': {'type': 'number'},
                    'preferred_vendor_id': {'type': 'string'}
                }
            },
            'description': 'Product data to update'
        }
    ],
    'responses': {
        '200': {'description': 'Product updated'},
        '400': {'description': 'Validation error or duplicate name'},
        '404': {'description': 'Product not found'}
    }
})
def update_product(product_id):
    data = request.get_json()

    # Field-level validation (update mode — only validate fields present in payload)
    errors = _validate_product_fields(data, is_update=True)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 400

    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    item = items[0]
    if item.get('is_deleted', False):
        return jsonify({'error': 'Product not found'}), 404

    # Duplicate name check (exclude current item)
    if 'name' in data:
        new_name = str(data['name']).strip()
        if _name_exists(new_name, exclude_id=product_id):
            return jsonify({
                'error': 'An item with this name already exists',
                'field': 'name'
            }), 400

    for field in [
        'item_type', 'name', 'hsn_sac', 'tax_preference', 'description', 'purchase_description',
        'category', 'price', 'purchase_rate', 'tax_rate', 'unit', 'sales_enabled', 'purchase_enabled',
        'sales_account', 'purchase_account', 'reorder_level', 'reorder_qty', 'preferred_vendor_id'
    ]:
        if field in data:
            item[field] = data[field]
    item['updated_at'] = datetime.utcnow().isoformat()
    products_container.replace_item(item=item['id'], body=item)
    return jsonify(item)


# ─────────────────────────────────────────────
#  SOFT DELETE
# ─────────────────────────────────────────────
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
            'description': 'Product soft-deleted',
            'examples': {'application/json': {'message': 'Product deleted'}}
        },
        '400': {
            'description': 'Item is used in invoices',
        },
        '404': {
            'description': 'Product not found',
        }
    }
})
def delete_product(product_id):
    query = f"SELECT * FROM c WHERE c.id = '{product_id}'"
    items = list(products_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Product not found'}), 404
    item = items[0]
    if item.get('is_deleted', False):
        return jsonify({'error': 'Product not found'}), 404

    # Check if item is referenced in any invoice
    invoice_count = _item_used_in_invoices(product_id)
    if invoice_count > 0:
        return jsonify({
            'error': f'This item is used in {invoice_count} invoice(s). Remove it from invoices before deleting, or archive it instead.',
            'invoice_count': invoice_count,
            'warning': True
        }), 400

    # Soft delete: mark as deleted instead of removing from DB
    now = datetime.utcnow().isoformat()
    item['is_deleted'] = True
    item['deleted_at'] = now
    item['updated_at'] = now
    products_container.replace_item(item=item['id'], body=item)
    return jsonify({'message': 'Product deleted'})


# ─────────────────────────────────────────────
#  STOCK SUMMARY
# ─────────────────────────────────────────────
@product_blueprint.route('/products/stock-summary', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'Stock summary for all products',
        }
    }
})
def products_stock_summary():
    products = list(products_container.read_all_items())
    products = [p for p in products if not p.get('is_deleted', False)]
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


# ─────────────────────────────────────────────
#  LOW STOCK
# ─────────────────────────────────────────────
@product_blueprint.route('/products/low-stock', methods=['GET'])
@swag_from({
    'tags': ['Products'],
    'responses': {
        '200': {
            'description': 'Products with low stock (at or below reorder level)',
        }
    }
})
def get_low_stock_products():
    """Get all products where current stock is at or below reorder level"""
    products = list(products_container.read_all_items())
    products = [p for p in products if not p.get('is_deleted', False)]
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


# ─────────────────────────────────────────────
#  RESTOCK (Create PO)
# ─────────────────────────────────────────────
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
        '201': {'description': 'Purchase order created for restocking'},
        '404': {'description': 'Product not found'},
        '400': {'description': 'Invalid request - missing vendor or reorder quantity'}
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
    if product.get('is_deleted', False):
        return jsonify({'error': 'Product not found'}), 404

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

from flask import Blueprint, jsonify, request
from flasgger import swag_from
from datetime import datetime, timedelta
from smart_invoice_pro.utils.cosmos_client import customers_container, products_container, invoices_container, get_container

stock_container = get_container("stock", "/product_id")
dashboard_blueprint = Blueprint('dashboard', __name__)

@dashboard_blueprint.route('/dashboard/summary', methods=['GET'])
@swag_from({
    'tags': ['Dashboard'],
    'responses': {
        '200': {
            'description': 'Dashboard summary',
            'examples': {
                'application/json': {
                    'total_customers': 10,
                    'total_products': 20,
                    'total_invoices': 30,
                    'total_revenue': 50000.0
                }
            }
        },
        '500': {
            'description': 'Error',
            'examples': {'application/json': {'error': 'Error fetching dashboard summary: ...'}}
        }
    }
})
def dashboard_summary():
    try:
        total_customers = len(list(customers_container.read_all_items()))
        total_products = len(list(products_container.read_all_items()))
        invoices = list(invoices_container.read_all_items())
        total_invoices = len(invoices)
        total_revenue = sum(float(inv.get('total_amount', 0)) for inv in invoices)
        return jsonify({
            'total_customers': total_customers,
            'total_products': total_products,
            'total_invoices': total_invoices,
            'total_revenue': total_revenue
        })
    except Exception as e:
        return jsonify({'error': f'Error fetching dashboard summary: {str(e)}'}), 500

@dashboard_blueprint.route('/dashboard/low-stock', methods=['GET'])
@swag_from({
    'tags': ['Dashboard'],
    'parameters': [
        {
            'name': 'threshold',
            'in': 'query',
            'type': 'number',
            'required': False,
            'description': 'Stock threshold (default 10)'
        }
    ],
    'responses': {
        '200': {
            'description': 'Low stock products',
            'examples': {
                'application/json': [
                    {
                        'product_id': 'uuid',
                        'name': 'Product A',
                        'stock': 5,
                        'reorder_level': 10
                    }
                ]
            }
        },
        '500': {
            'description': 'Error',
            'examples': {'application/json': {'error': 'Error fetching low stock products: ...'}}
        }
    }
})
def dashboard_low_stock():
    try:
        threshold = float(request.args.get('threshold', 10))
        products = list(products_container.read_all_items())
        low_stock = []
        for product in products:
            product_id = product['id']
            reorder_level = float(product.get('reorder_level', threshold))
            query = f"SELECT c.type, c.quantity FROM c WHERE c.product_id = '{product_id}'"
            stock_items = list(stock_container.query_items(query=query, enable_cross_partition_query=True))
            stock_in = sum(item['quantity'] for item in stock_items if item['type'] == 'IN')
            stock_out = sum(item['quantity'] for item in stock_items if item['type'] == 'OUT')
            current_stock = stock_in - stock_out
            if current_stock < reorder_level:
                low_stock.append({
                    'product_id': product_id,
                    'name': product.get('name', ''),
                    'stock': current_stock,
                    'reorder_level': reorder_level
                })
        return jsonify(low_stock)
    except Exception as e:
        return jsonify({'error': f'Error fetching low stock products: {str(e)}'}), 500

@dashboard_blueprint.route('/dashboard/monthly-revenue', methods=['GET'])
@swag_from({
    'tags': ['Dashboard'],
    'responses': {
        '200': {
            'description': 'Monthly revenue for last 6 months',
            'examples': {
                'application/json': [
                    {'month': '2025-01', 'revenue': 10000.0},
                    {'month': '2025-02', 'revenue': 12000.0}
                ]
            }
        },
        '500': {
            'description': 'Error',
            'examples': {'application/json': {'error': 'Error fetching monthly revenue: ...'}}
        }
    }
})
def dashboard_monthly_revenue():
    try:
        invoices = list(invoices_container.read_all_items())
        now = datetime.utcnow()
        monthly = {}
        for i in range(6):
            month = (now - timedelta(days=now.day-1)).replace(day=1) - timedelta(days=30*i)
            key = month.strftime('%Y-%m')
            monthly[key] = 0.0
        for inv in invoices:
            date_str = inv.get('created_at') or inv.get('issue_date')
            if not date_str:
                continue
            try:
                dt = datetime.fromisoformat(date_str[:19])
            except Exception:
                continue
            key = dt.strftime('%Y-%m')
            if key in monthly:
                monthly[key] += float(inv.get('total_amount', 0))
        result = [{'month': k, 'revenue': monthly[k]} for k in sorted(monthly.keys())[-6:]]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Error fetching monthly revenue: {str(e)}'}), 500

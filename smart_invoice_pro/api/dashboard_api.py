from flask import Blueprint, jsonify, request
from flasgger import swag_from
from datetime import datetime, timedelta
from smart_invoice_pro.utils.cosmos_client import customers_container, products_container, invoices_container, bills_container, get_container

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
        range_type = request.args.get('range', 'all')
        today = datetime.utcnow().date()

        def parse_iso_date(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value[:10]).date()
            except Exception:
                return None

        # Determine date bounds for filtering
        if range_type == 'this_week':
            start_date = today - timedelta(days=6)
            end_date = today
        elif range_type == 'this_month':
            start_date = today.replace(day=1)
            end_date = today
        elif range_type == 'this_quarter':
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
            end_date = today
        elif range_type == 'this_year':
            start_date = today.replace(month=1, day=1)
            end_date = today
        elif range_type == 'custom':
            start_date = parse_iso_date(request.args.get('start_date'))
            end_date = parse_iso_date(request.args.get('end_date'))
            if not start_date or not end_date:
                return jsonify({'error': 'start_date and end_date are required for custom range'}), 400
            if start_date > end_date:
                return jsonify({'error': 'start_date cannot be after end_date'}), 400
        else:
            start_date = None
            end_date = None

        def invoice_in_range(inv):
            if start_date is None:
                return True
            date_str = inv.get('issue_date') or inv.get('created_at')
            if not date_str:
                return False
            try:
                inv_date = datetime.fromisoformat(date_str[:10]).date()
                return start_date <= inv_date <= end_date
            except Exception:
                return False

        def bill_in_range(b):
            if start_date is None:
                return True
            date_str = b.get('bill_date') or b.get('issue_date') or b.get('created_at')
            if not date_str:
                return False
            try:
                bill_date = datetime.fromisoformat(date_str[:10]).date()
                return start_date <= bill_date <= end_date
            except Exception:
                return False

        total_customers = len(list(customers_container.read_all_items()))
        total_products = len(list(products_container.read_all_items()))
        all_invoices = list(invoices_container.read_all_items())
        invoices = [inv for inv in all_invoices if invoice_in_range(inv)]

        total_invoices = len(invoices)
        total_revenue = sum(float(inv.get('total_amount', 0)) for inv in invoices)

        # Receivables: balance_due on unpaid/partially-paid invoices in range
        receivable_statuses = {'issued', 'partially paid', 'overdue', 'sent'}
        total_receivables = sum(
            float(inv.get('balance_due', inv.get('total_amount', 0)))
            for inv in invoices
            if inv.get('status', '').lower() in receivable_statuses
        )

        # Overdue: due_date is past and invoice is still open (within range)
        overdue_count = 0
        for inv in invoices:
            if inv.get('status', '').lower() not in {'issued', 'partially paid', 'sent', 'overdue'}:
                continue
            due_str = inv.get('due_date')
            if not due_str:
                continue
            try:
                if datetime.fromisoformat(due_str[:10]).date() < today:
                    overdue_count += 1
            except Exception:
                pass

        # Payables: balance_due on unpaid bills in range
        payable_statuses = {'unpaid', 'partially paid', 'overdue'}
        all_bills = list(bills_container.read_all_items())
        bills = [b for b in all_bills if bill_in_range(b)]
        total_payables = sum(
            float(b.get('balance_due', b.get('total_amount', 0)))
            for b in bills
            if b.get('payment_status', '').lower() in payable_statuses
        )

        return jsonify({
            'total_customers': total_customers,
            'total_products': total_products,
            'total_invoices': total_invoices,
            'total_revenue': total_revenue,
            'total_receivables': total_receivables,
            'total_payables': total_payables,
            'overdue_count': overdue_count,
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
        range_type = request.args.get('range', 'this_year')
        now = datetime.utcnow()
        today = now.date()

        def parse_iso_date(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value[:10]).date()
            except Exception:
                return None

        if range_type == 'this_week':
            start_date = today - timedelta(days=6)
            end_date = today
        elif range_type == 'this_month':
            start_date = today.replace(day=1)
            end_date = today
        elif range_type == 'this_quarter':
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
            end_date = today
        elif range_type == 'custom':
            start_date = parse_iso_date(request.args.get('start_date'))
            end_date = parse_iso_date(request.args.get('end_date'))
            if not start_date or not end_date:
                return jsonify({'error': 'start_date and end_date are required for custom range'}), 400
            if start_date > end_date:
                return jsonify({'error': 'start_date cannot be after end_date'}), 400
        else:
            start_date = today.replace(month=1, day=1)
            end_date = today

        # Build monthly buckets across the selected date range.
        monthly = {}
        month_cursor = start_date.replace(day=1)
        range_end_month = end_date.replace(day=1)
        while month_cursor <= range_end_month:
            key = month_cursor.strftime('%Y-%m')
            monthly[key] = 0.0
            if month_cursor.month == 12:
                month_cursor = month_cursor.replace(year=month_cursor.year + 1, month=1, day=1)
            else:
                month_cursor = month_cursor.replace(month=month_cursor.month + 1, day=1)

        invoices = list(invoices_container.read_all_items())

        for inv in invoices:
            date_str = inv.get('created_at') or inv.get('issue_date')
            if not date_str:
                continue
            try:
                dt = datetime.fromisoformat(date_str[:19])
            except Exception:
                continue

            inv_date = dt.date()
            if inv_date < start_date or inv_date > end_date:
                continue

            key = dt.strftime('%Y-%m')
            if key in monthly:
                monthly[key] += float(inv.get('total_amount', 0))

        result = [{'month': k, 'revenue': monthly[k]} for k in sorted(monthly.keys())]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Error fetching monthly revenue: {str(e)}'}), 500


@dashboard_blueprint.route('/dashboard/recent-invoices', methods=['GET'])
def dashboard_recent_invoices():
    """Return the 10 most recent invoices for the dashboard activity feed."""
    try:
        limit = int(request.args.get('limit', 10))
        invoices = list(invoices_container.read_all_items())
        # Sort by created_at or issue_date descending
        def sort_key(inv):
            ds = inv.get('created_at') or inv.get('issue_date') or ''
            return ds[:19]
        invoices.sort(key=sort_key, reverse=True)
        recent = invoices[:limit]
        result = []
        for inv in recent:
            result.append({
                'id': inv.get('id'),
                'invoice_number': inv.get('invoice_number', ''),
                'customer_name': inv.get('customer_name', ''),
                'total_amount': float(inv.get('total_amount', 0)),
                'balance_due': float(inv.get('balance_due', inv.get('total_amount', 0))),
                'status': inv.get('status', ''),
                'issue_date': (inv.get('issue_date') or inv.get('created_at') or '')[:10],
                'due_date': (inv.get('due_date') or '')[:10],
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Error fetching recent invoices: {str(e)}'}), 500

from flask import Blueprint, jsonify, request, current_app
from flasgger import swag_from
from datetime import datetime, timedelta
from smart_invoice_pro.utils.cosmos_client import (
    customers_container,
    products_container,
    invoices_container,
    bills_container,
    expenses_container,
    stock_container,
)

dashboard_blueprint = Blueprint('dashboard', __name__)

_SUMMARY_CACHE = {}
_SUMMARY_CACHE_TTL_SECONDS = 180

OPEN_INVOICE_STATUSES = {'issued', 'partially paid', 'overdue', 'sent'}
OPEN_BILL_STATUSES = {'unpaid', 'partially paid', 'overdue'}


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _doc_date(document, fields):
    for field in fields:
        parsed = _parse_iso_date(document.get(field))
        if parsed:
            return parsed
    return None


def _filter_docs_by_period(documents, date_fields, start_date, end_date):
    filtered = []
    for document in documents:
        doc_date = _doc_date(document, date_fields)
        if not doc_date:
            continue
        if start_date <= doc_date <= end_date:
            filtered.append(document)
    return filtered


def _metric_payload(current_value, previous_value):
    change = 0.0 if previous_value == 0 else ((current_value - previous_value) / previous_value) * 100
    return {
        'value': current_value,
        'previous_value': previous_value,
        'percentage_change': round(change, 2),
    }


def _period_label(range_type, start_date, end_date):
    if range_type == 'this_week':
        return 'This Week'
    if range_type == 'this_month':
        return 'This Month'
    if range_type == 'this_quarter':
        return 'This Quarter'
    if range_type == 'this_year':
        return 'This Year'
    return f"{start_date.isoformat()} to {end_date.isoformat()}"


def _resolve_period_from_request(range_type, args, today):
    if range_type == 'this_week':
        return today - timedelta(days=6), today, None
    if range_type == 'this_month':
        return today.replace(day=1), today, None
    if range_type == 'this_quarter':
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=quarter_start_month, day=1), today, None
    if range_type == 'this_year':
        return today.replace(month=1, day=1), today, None
    if range_type == 'custom':
        start_date = _parse_iso_date(args.get('start_date'))
        end_date = _parse_iso_date(args.get('end_date'))
        if not start_date or not end_date:
            return None, None, 'start_date and end_date are required for custom range'
        if start_date > end_date:
            return None, None, 'start_date cannot be after end_date'
        return start_date, end_date, None
    return None, None, 'Invalid range. Supported: this_week, this_month, this_quarter, this_year, custom'


def _previous_period(start_date, end_date):
    day_count = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=day_count - 1)
    return previous_start, previous_end


def _tenant_docs(container, tenant_id):
    docs = list(container.read_all_items())
    if not tenant_id:
        return docs
    return [doc for doc in docs if doc.get('tenant_id') == tenant_id]


def _invoice_payments_in_period(invoices, start_date, end_date):
    total = 0.0
    for invoice in invoices:
        payment_history = invoice.get('payment_history')
        if isinstance(payment_history, list) and payment_history:
            for payment in payment_history:
                payment_date = _doc_date(payment, ['payment_date', 'paid_date', 'created_at', 'date'])
                if payment_date and start_date <= payment_date <= end_date:
                    total += _safe_float(
                        payment.get('amount')
                        or payment.get('paid_amount')
                        or payment.get('amount_paid')
                    )
            continue

        invoice_date = _doc_date(invoice, ['created_at', 'issue_date'])
        if invoice_date and start_date <= invoice_date <= end_date:
            total += _safe_float(invoice.get('amount_paid'))

    return total


def _summary_cache_key(tenant_id, range_type, args):
    return (
        tenant_id or 'public',
        range_type,
        args.get('start_date') or '',
        args.get('end_date') or '',
    )


def _summary_cache_get(key):
    cached = _SUMMARY_CACHE.get(key)
    if not cached:
        return None
    if cached['expires_at'] < datetime.utcnow().timestamp():
        _SUMMARY_CACHE.pop(key, None)
        return None


# ── Revenue chart helpers ─────────────────────────────────────────────────────

def _chart_granularity(start_date, end_date):
    """Return 'day', 'week', or 'month' based on span length."""
    span = (end_date - start_date).days
    if span <= 31:
        return 'day'
    if span <= 92:
        return 'week'
    return 'month'


def _build_chart_buckets(granularity, start_date, end_date):
    """Return list of (key, label, bucket_start, bucket_end) for the given period."""
    buckets = []
    if granularity == 'day':
        cursor = start_date
        while cursor <= end_date:
            key = cursor.strftime('%Y-%m-%d')
            label = cursor.strftime('%d %b')
            buckets.append((key, label, cursor, cursor))
            cursor += timedelta(days=1)
    elif granularity == 'week':
        cursor = start_date
        while cursor <= end_date:
            week_end = min(cursor + timedelta(days=6), end_date)
            key = cursor.strftime('%Y-W%V')
            label = f"{cursor.strftime('%d %b')} – {week_end.strftime('%d %b')}"
            buckets.append((key, label, cursor, week_end))
            cursor = week_end + timedelta(days=1)
    else:  # month
        month_cursor = start_date.replace(day=1)
        while month_cursor <= end_date:
            key = month_cursor.strftime('%Y-%m')
            label = month_cursor.strftime('%b %Y')
            if month_cursor.month == 12:
                next_month = month_cursor.replace(year=month_cursor.year + 1, month=1, day=1)
            else:
                next_month = month_cursor.replace(month=month_cursor.month + 1, day=1)
            bucket_end = min(next_month - timedelta(days=1), end_date)
            buckets.append((key, label, month_cursor, bucket_end))
            month_cursor = next_month
    return buckets


def _revenue_by_chart_buckets(invoices, buckets):
    """Sum total_amount per (bucket_start, bucket_end) bucket."""
    totals = {key: 0.0 for key, _, _, _ in buckets}
    for inv in invoices:
        inv_date = _doc_date(inv, ['created_at', 'issue_date'])
        if not inv_date:
            continue
        amount = _safe_float(inv.get('total_amount'))
        for key, _, b_start, b_end in buckets:
            if b_start <= inv_date <= b_end:
                totals[key] += amount
                break
    return totals
    return cached['payload']


def _summary_cache_set(key, payload):
    _SUMMARY_CACHE[key] = {
        'payload': payload,
        'expires_at': datetime.utcnow().timestamp() + _SUMMARY_CACHE_TTL_SECONDS,
    }

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
        range_type = request.args.get('range', 'this_year')
        today = datetime.utcnow().date()
        tenant_id = getattr(request, 'tenant_id', None)
        use_cache = not bool(current_app.config.get('TESTING'))
        cache_key = _summary_cache_key(tenant_id, range_type, request.args)
        if use_cache:
            cached_payload = _summary_cache_get(cache_key)
            if cached_payload:
                return jsonify(cached_payload)

        start_date, end_date, error = _resolve_period_from_request(range_type, request.args, today)
        if error:
            return jsonify({'error': error}), 400

        previous_start, previous_end = _previous_period(start_date, end_date)

        all_customers = _tenant_docs(customers_container, tenant_id)
        all_products = _tenant_docs(products_container, tenant_id)
        all_invoices = _tenant_docs(invoices_container, tenant_id)
        all_bills = _tenant_docs(bills_container, tenant_id)
        all_expenses = _tenant_docs(expenses_container, tenant_id)

        current_customers = _filter_docs_by_period(all_customers, ['created_at', 'customer_since'], start_date, end_date)
        previous_customers = _filter_docs_by_period(all_customers, ['created_at', 'customer_since'], previous_start, previous_end)

        current_invoices = _filter_docs_by_period(all_invoices, ['created_at', 'issue_date'], start_date, end_date)
        previous_invoices = _filter_docs_by_period(all_invoices, ['created_at', 'issue_date'], previous_start, previous_end)

        current_bills = _filter_docs_by_period(all_bills, ['created_at', 'bill_date', 'issue_date'], start_date, end_date)
        previous_bills = _filter_docs_by_period(all_bills, ['created_at', 'bill_date', 'issue_date'], previous_start, previous_end)

        current_expenses = _filter_docs_by_period(all_expenses, ['created_at', 'expense_date', 'date'], start_date, end_date)
        previous_expenses = _filter_docs_by_period(all_expenses, ['created_at', 'expense_date', 'date'], previous_start, previous_end)

        customers_added_current = len(current_customers)
        customers_added_previous = len(previous_customers)

        invoices_created_current = len(current_invoices)
        invoices_created_previous = len(previous_invoices)

        revenue_current = sum(_safe_float(inv.get('total_amount')) for inv in current_invoices)
        revenue_previous = sum(_safe_float(inv.get('total_amount')) for inv in previous_invoices)

        payments_current = _invoice_payments_in_period(all_invoices, start_date, end_date)
        payments_previous = _invoice_payments_in_period(all_invoices, previous_start, previous_end)

        receivables_current = sum(
            _safe_float(inv.get('balance_due', inv.get('total_amount')))
            for inv in current_invoices
            if str(inv.get('status', '')).lower() in OPEN_INVOICE_STATUSES
        )
        receivables_previous = sum(
            _safe_float(inv.get('balance_due', inv.get('total_amount')))
            for inv in previous_invoices
            if str(inv.get('status', '')).lower() in OPEN_INVOICE_STATUSES
        )

        bill_payables_current = sum(
            _safe_float(bill.get('balance_due', bill.get('total_amount')))
            for bill in current_bills
            if str(bill.get('payment_status', '')).lower() in OPEN_BILL_STATUSES
        )
        bill_payables_previous = sum(
            _safe_float(bill.get('balance_due', bill.get('total_amount')))
            for bill in previous_bills
            if str(bill.get('payment_status', '')).lower() in OPEN_BILL_STATUSES
        )
        expense_total_current = sum(_safe_float(exp.get('amount', exp.get('total_amount'))) for exp in current_expenses)
        expense_total_previous = sum(_safe_float(exp.get('amount', exp.get('total_amount'))) for exp in previous_expenses)

        payables_current = bill_payables_current + expense_total_current
        payables_previous = bill_payables_previous + expense_total_previous

        overdue_count = 0
        for inv in all_invoices:
            if str(inv.get('status', '')).lower() not in OPEN_INVOICE_STATUSES:
                continue
            due_date = _parse_iso_date(inv.get('due_date'))
            if due_date and due_date < today:
                overdue_count += 1

        metrics = {
            'customers_added': _metric_payload(customers_added_current, customers_added_previous),
            'invoices_created': _metric_payload(invoices_created_current, invoices_created_previous),
            'revenue': _metric_payload(revenue_current, revenue_previous),
            'payments_received': _metric_payload(payments_current, payments_previous),
            'receivables': _metric_payload(receivables_current, receivables_previous),
            'payables': _metric_payload(payables_current, payables_previous),
            'overdue_invoices_current': {
                'value': overdue_count,
                'is_time_based': False,
            },
            'total_customers': {
                'value': len(all_customers),
                'is_time_based': False,
            },
            'total_products': {
                'value': len(all_products),
                'is_time_based': False,
            },
        }

        payload = {
            'range': range_type,
            'period': {
                'current': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'label': _period_label(range_type, start_date, end_date),
                },
                'previous': {
                    'start_date': previous_start.isoformat(),
                    'end_date': previous_end.isoformat(),
                    'label': f"Previous {_period_label(range_type, start_date, end_date)}",
                },
            },
            'metrics': metrics,
            # Backward-compatible aliases consumed by older clients.
            'customers_added': metrics['customers_added']['value'],
            'invoices_created': metrics['invoices_created']['value'],
            'revenue': metrics['revenue']['value'],
            'payments_received': metrics['payments_received']['value'],
            'receivables': metrics['receivables']['value'],
            'payables': metrics['payables']['value'],
            'overdue_count': overdue_count,
            'total_customers': len(all_customers),
            'total_products': len(all_products),
            'total_invoices': invoices_created_current,
            'total_revenue': revenue_current,
            'total_receivables': receivables_current,
            'total_payables': payables_current,
        }
        if use_cache:
            _summary_cache_set(cache_key, payload)
        return jsonify(payload)
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
        tenant_id = getattr(request, 'tenant_id', None)
        products = _tenant_docs(products_container, tenant_id)
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
        today = datetime.utcnow().date()
        tenant_id = getattr(request, 'tenant_id', None)

        start_date, end_date, error = _resolve_period_from_request(range_type, request.args, today)
        if error:
            return jsonify({'error': error}), 400

        previous_start, previous_end = _previous_period(start_date, end_date)
        granularity = _chart_granularity(start_date, end_date)
        buckets = _build_chart_buckets(granularity, start_date, end_date)

        # Shift each bucket back by the same period offset to get the previous window
        period_offset = timedelta(days=(end_date - start_date).days + 1)
        prev_buckets = [
            (key, label, b_start - period_offset, b_end - period_offset)
            for key, label, b_start, b_end in buckets
        ]

        invoices = _tenant_docs(invoices_container, tenant_id)
        current_totals = _revenue_by_chart_buckets(invoices, buckets)
        prev_totals = _revenue_by_chart_buckets(invoices, prev_buckets)

        result = []
        for key, label, _, _ in buckets:
            curr = current_totals[key]
            prev = prev_totals[key]
            change = 0.0 if prev == 0 else round(((curr - prev) / prev) * 100, 2)
            result.append({
                'period_key': key,
                'label': label,
                'month': key,           # backward-compat alias used by older consumers
                'revenue': curr,
                'previous_revenue': prev,
                'percentage_change': change,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Error fetching monthly revenue: {str(e)}'}), 500


@dashboard_blueprint.route('/dashboard/recent-invoices', methods=['GET'])
def dashboard_recent_invoices():
    """Return the 10 most recent invoices for the dashboard activity feed."""
    try:
        limit = int(request.args.get('limit', 10))
        range_type = request.args.get('range')
        tenant_id = getattr(request, 'tenant_id', None)
        invoices = _tenant_docs(invoices_container, tenant_id)

        if range_type:
            today = datetime.utcnow().date()
            start_date, end_date, error = _resolve_period_from_request(range_type, request.args, today)
            if error:
                return jsonify({'error': error}), 400
            invoices = _filter_docs_by_period(invoices, ['created_at', 'issue_date'], start_date, end_date)

        # Sort by created_at or issue_date descending
        def sort_key(inv):
            doc_date = _doc_date(inv, ['created_at', 'issue_date'])
            return doc_date.isoformat() if doc_date else ''

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

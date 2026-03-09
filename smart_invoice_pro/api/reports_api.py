from flask import Blueprint, jsonify, request
from smart_invoice_pro.utils.cosmos_client import get_container
from datetime import datetime, timedelta
from flasgger import swag_from
import os
from collections import defaultdict

reports_blueprint = Blueprint('reports', __name__)


def parse_date(date_str):
    """Parse date string to datetime object"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except:
        return None


@reports_blueprint.route('/api/reports/profit-loss', methods=['GET'])
@swag_from({
    'summary': 'Get Profit & Loss Report',
    'description': 'Generate profit and loss statement for a date range',
    'parameters': [
        {
            'name': 'start_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Start date (YYYY-MM-DD), defaults to start of current year'
        },
        {
            'name': 'end_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'End date (YYYY-MM-DD), defaults to today'
        },
        {
            'name': 'user_id',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'User ID'
        }
    ],
    'responses': {
        200: {
            'description': 'Profit & Loss report data',
            'schema': {
                'type': 'object',
                'properties': {
                    'period': {'type': 'object'},
                    'revenue': {'type': 'object'},
                    'cost_of_goods_sold': {'type': 'object'},
                    'gross_profit': {'type': 'number'},
                    'expenses': {'type': 'object'},
                    'net_profit': {'type': 'number'}
                }
            }
        }
    }
})
def get_profit_loss():
    """Get Profit & Loss Statement"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        # Parse dates
        end_date = parse_date(request.args.get('end_date')) or datetime.now()
        start_date = parse_date(request.args.get('start_date')) or datetime(end_date.year, 1, 1)

        # Get containers
        invoices_container = get_container('invoices')
        expenses_container = get_container('expenses')
        bills_container = get_container('bills')

        # Query invoices (Revenue)
        invoice_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.issue_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.issue_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        invoices = list(invoices_container.query_items(query=invoice_query, enable_cross_partition_query=True))

        # Calculate revenue by category
        revenue_total = 0
        revenue_by_category = defaultdict(float)
        
        for invoice in invoices:
            if invoice.get('status') in ['Paid', 'Partially Paid']:
                amount = float(invoice.get('amount_paid', 0))
                revenue_total += amount
                # You can categorize by product categories from items
                revenue_by_category['Sales Revenue'] += amount

        # Query bills (Cost of Goods Sold)
        bills_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.bill_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.bill_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        bills = list(bills_container.query_items(query=bills_query, enable_cross_partition_query=True))

        cogs_total = 0
        cogs_by_category = defaultdict(float)
        
        for bill in bills:
            if bill.get('status') in ['Paid', 'Partially Paid']:
                amount = float(bill.get('amount_paid', 0))
                cogs_total += amount
                cogs_by_category['Purchases'] += amount

        # Query expenses (Operating Expenses)
        expenses_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.expense_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.expense_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        expenses = list(expenses_container.query_items(query=expenses_query, enable_cross_partition_query=True))

        expenses_total = 0
        expenses_by_category = defaultdict(float)
        
        for expense in expenses:
            amount = float(expense.get('amount', 0))
            expenses_total += amount
            category = expense.get('category', 'Other')
            expenses_by_category[category] += amount

        # Calculate profit
        gross_profit = revenue_total - cogs_total
        net_profit = gross_profit - expenses_total
        gross_margin = (gross_profit / revenue_total * 100) if revenue_total > 0 else 0
        net_margin = (net_profit / revenue_total * 100) if revenue_total > 0 else 0

        return jsonify({
            'period': {
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d')
            },
            'revenue': {
                'total': round(revenue_total, 2),
                'by_category': dict(revenue_by_category),
                'invoice_count': len(invoices)
            },
            'cost_of_goods_sold': {
                'total': round(cogs_total, 2),
                'by_category': dict(cogs_by_category),
                'bill_count': len(bills)
            },
            'gross_profit': round(gross_profit, 2),
            'gross_margin': round(gross_margin, 2),
            'expenses': {
                'total': round(expenses_total, 2),
                'by_category': dict(expenses_by_category),
                'expense_count': len(expenses)
            },
            'net_profit': round(net_profit, 2),
            'net_margin': round(net_margin, 2)
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_blueprint.route('/api/reports/balance-sheet', methods=['GET'])
@swag_from({
    'summary': 'Get Balance Sheet',
    'description': 'Generate balance sheet as of a specific date',
    'parameters': [
        {
            'name': 'as_of_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'As of date (YYYY-MM-DD), defaults to today'
        },
        {
            'name': 'user_id',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'User ID'
        }
    ],
    'responses': {
        200: {
            'description': 'Balance sheet data'
        }
    }
})
def get_balance_sheet():
    """Get Balance Sheet"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        as_of_date = parse_date(request.args.get('as_of_date')) or datetime.now()

        # Get containers
        invoices_container = get_container('invoices')
        expenses_container = get_container('expenses')
        bills_container = get_container('bills')
        products_container = get_container('products')
        bank_accounts_container = get_container('bank_accounts')

        # Assets
        # 1. Cash (from bank accounts)
        bank_query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
        bank_accounts = list(bank_accounts_container.query_items(query=bank_query, enable_cross_partition_query=True))
        cash_total = sum(float(acc.get('balance', 0)) for acc in bank_accounts)

        # 2. Accounts Receivable (unpaid invoices)
        ar_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.issue_date <= '{as_of_date.strftime('%Y-%m-%d')}'
            AND c.status IN ('Pending', 'Partially Paid')
        """
        ar_invoices = list(invoices_container.query_items(query=ar_query, enable_cross_partition_query=True))
        accounts_receivable = sum(float(inv.get('balance_due', 0)) for inv in ar_invoices)

        # 3. Inventory (available products)
        products_query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
        products = list(products_container.query_items(query=products_query, enable_cross_partition_query=True))
        inventory_value = 0
        for product in products:
            qty = float(product.get('availableQty', 0))
            cost = float(product.get('purchase_price', 0)) if product.get('purchase_price') else float(product.get('price', 0))
            inventory_value += qty * cost

        total_current_assets = cash_total + accounts_receivable + inventory_value

        # Liabilities
        # 1. Accounts Payable (unpaid bills)
        ap_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.bill_date <= '{as_of_date.strftime('%Y-%m-%d')}'
            AND c.status IN ('Pending', 'Partially Paid')
        """
        ap_bills = list(bills_container.query_items(query=ap_query, enable_cross_partition_query=True))
        accounts_payable = sum(float(bill.get('balance_due', 0)) for bill in ap_bills)

        total_current_liabilities = accounts_payable

        # Equity
        # Calculate from inception profit/loss
        all_invoices_query = f"""
            SELECT * FROM c WHERE c.user_id = '{user_id}' 
            AND c.issue_date <= '{as_of_date.strftime('%Y-%m-%d')}'
        """
        all_invoices = list(invoices_container.query_items(query=all_invoices_query, enable_cross_partition_query=True))
        total_revenue = sum(float(inv.get('amount_paid', 0)) for inv in all_invoices if inv.get('status') in ['Paid', 'Partially Paid'])

        all_expenses_query = f"""
            SELECT * FROM c WHERE c.user_id = '{user_id}' 
            AND c.expense_date <= '{as_of_date.strftime('%Y-%m-%d')}'
        """
        all_expenses = list(expenses_container.query_items(query=all_expenses_query, enable_cross_partition_query=True))
        total_expenses = sum(float(exp.get('amount', 0)) for exp in all_expenses)

        all_bills_query = f"""
            SELECT * FROM c WHERE c.user_id = '{user_id}' 
            AND c.bill_date <= '{as_of_date.strftime('%Y-%m-%d')}'
        """
        all_bills = list(bills_container.query_items(query=all_bills_query, enable_cross_partition_query=True))
        total_cogs = sum(float(bill.get('amount_paid', 0)) for bill in all_bills if bill.get('status') in ['Paid', 'Partially Paid'])

        retained_earnings = total_revenue - total_cogs - total_expenses
        total_equity = retained_earnings

        # Verify balance (Assets = Liabilities + Equity)
        total_assets = total_current_assets
        total_liabilities_equity = total_current_liabilities + total_equity

        return jsonify({
            'as_of_date': as_of_date.strftime('%Y-%m-%d'),
            'assets': {
                'current_assets': {
                    'cash': round(cash_total, 2),
                    'accounts_receivable': round(accounts_receivable, 2),
                    'inventory': round(inventory_value, 2),
                    'total': round(total_current_assets, 2)
                },
                'fixed_assets': {
                    'total': 0  # Can be extended for fixed assets
                },
                'total': round(total_assets, 2)
            },
            'liabilities': {
                'current_liabilities': {
                    'accounts_payable': round(accounts_payable, 2),
                    'total': round(total_current_liabilities, 2)
                },
                'long_term_liabilities': {
                    'total': 0  # Can be extended for long-term liabilities
                },
                'total': round(total_current_liabilities, 2)
            },
            'equity': {
                'retained_earnings': round(retained_earnings, 2),
                'total': round(total_equity, 2)
            },
            'total_liabilities_equity': round(total_liabilities_equity, 2),
            'balance_check': {
                'balanced': abs(total_assets - total_liabilities_equity) < 0.01,
                'difference': round(total_assets - total_liabilities_equity, 2)
            }
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_blueprint.route('/api/reports/aging', methods=['GET'])
@swag_from({
    'summary': 'Get Accounts Receivable Aging Report',
    'description': 'Generate A/R aging report showing unpaid invoices by age brackets',
    'parameters': [
        {
            'name': 'as_of_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'As of date (YYYY-MM-DD), defaults to today'
        },
        {
            'name': 'user_id',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'User ID'
        }
    ],
    'responses': {
        200: {
            'description': 'A/R aging report data'
        }
    }
})
def get_ar_aging():
    """Get Accounts Receivable Aging Report"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        as_of_date = parse_date(request.args.get('as_of_date')) or datetime.now()

        # Get containers
        invoices_container = get_container('invoices')
        customers_container = get_container('customers')

        # Query unpaid/partially paid invoices
        query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.status IN ('Pending', 'Partially Paid')
            AND c.issue_date <= '{as_of_date.strftime('%Y-%m-%d')}'
        """
        invoices = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))

        # Get customer names
        customers_query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
        customers = list(customers_container.query_items(query=customers_query, enable_cross_partition_query=True))
        customer_map = {c['id']: c.get('name', 'Unknown') for c in customers}

        # Age brackets: Current, 1-30, 31-60, 61-90, 90+
        aging_buckets = {
            'current': [],
            '1-30': [],
            '31-60': [],
            '61-90': [],
            '90+': []
        }

        aging_totals = {
            'current': 0,
            '1-30': 0,
            '31-60': 0,
            '61-90': 0,
            '90+': 0
        }

        for invoice in invoices:
            due_date = parse_date(invoice.get('due_date'))
            if not due_date:
                continue

            days_overdue = (as_of_date - due_date).days
            balance_due = float(invoice.get('balance_due', 0))

            invoice_data = {
                'invoice_id': invoice['id'],
                'invoice_number': invoice.get('invoice_number'),
                'customer_id': invoice.get('customer_id'),
                'customer_name': customer_map.get(invoice.get('customer_id'), 'Unknown'),
                'issue_date': invoice.get('issue_date'),
                'due_date': invoice.get('due_date'),
                'total_amount': float(invoice.get('total_amount', 0)),
                'amount_paid': float(invoice.get('amount_paid', 0)),
                'balance_due': balance_due,
                'days_overdue': days_overdue,
                'status': invoice.get('status')
            }

            if days_overdue < 0:
                bucket = 'current'
            elif days_overdue <= 30:
                bucket = '1-30'
            elif days_overdue <= 60:
                bucket = '31-60'
            elif days_overdue <= 90:
                bucket = '61-90'
            else:
                bucket = '90+'

            aging_buckets[bucket].append(invoice_data)
            aging_totals[bucket] += balance_due

        total_outstanding = sum(aging_totals.values())

        # Customer summary
        customer_summary = defaultdict(float)
        for invoice in invoices:
            customer_id = invoice.get('customer_id')
            balance_due = float(invoice.get('balance_due', 0))
            customer_summary[customer_id] += balance_due

        customer_aging = []
        for customer_id, total in customer_summary.items():
            customer_aging.append({
                'customer_id': customer_id,
                'customer_name': customer_map.get(customer_id, 'Unknown'),
                'total_outstanding': round(total, 2)
            })

        customer_aging.sort(key=lambda x: x['total_outstanding'], reverse=True)

        return jsonify({
            'as_of_date': as_of_date.strftime('%Y-%m-%d'),
            'aging_buckets': {
                'current': {
                    'total': round(aging_totals['current'], 2),
                    'count': len(aging_buckets['current']),
                    'invoices': aging_buckets['current']
                },
                '1-30': {
                    'total': round(aging_totals['1-30'], 2),
                    'count': len(aging_buckets['1-30']),
                    'invoices': aging_buckets['1-30']
                },
                '31-60': {
                    'total': round(aging_totals['31-60'], 2),
                    'count': len(aging_buckets['31-60']),
                    'invoices': aging_buckets['31-60']
                },
                '61-90': {
                    'total': round(aging_totals['61-90'], 2),
                    'count': len(aging_buckets['61-90']),
                    'invoices': aging_buckets['61-90']
                },
                '90+': {
                    'total': round(aging_totals['90+'], 2),
                    'count': len(aging_buckets['90+']),
                    'invoices': aging_buckets['90+']
                }
            },
            'total_outstanding': round(total_outstanding, 2),
            'total_invoices': len(invoices),
            'customer_summary': customer_aging
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_blueprint.route('/api/reports/cash-flow', methods=['GET'])
@swag_from({
    'summary': 'Get Cash Flow Report',
    'description': 'Generate cash flow statement for a date range',
    'parameters': [
        {
            'name': 'start_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Start date (YYYY-MM-DD)'
        },
        {
            'name': 'end_date',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'End date (YYYY-MM-DD)'
        },
        {
            'name': 'user_id',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'User ID'
        }
    ],
    'responses': {
        200: {
            'description': 'Cash flow report data'
        }
    }
})
def get_cash_flow():
    """Get Cash Flow Statement"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        end_date = parse_date(request.args.get('end_date')) or datetime.now()
        start_date = parse_date(request.args.get('start_date')) or datetime(end_date.year, 1, 1)

        # Get containers
        invoices_container = get_container('invoices')
        expenses_container = get_container('expenses')
        bills_container = get_container('bills')

        # Cash from Operating Activities
        # Cash received from customers (paid invoices)
        invoices_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.issue_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.issue_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        invoices = list(invoices_container.query_items(query=invoices_query, enable_cross_partition_query=True))
        cash_received = sum(float(inv.get('amount_paid', 0)) for inv in invoices)

        # Cash paid for expenses
        expenses_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.expense_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.expense_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        expenses = list(expenses_container.query_items(query=expenses_query, enable_cross_partition_query=True))
        cash_paid_expenses = sum(float(exp.get('amount', 0)) for exp in expenses)

        # Cash paid to suppliers (paid bills)
        bills_query = f"""
            SELECT * FROM c 
            WHERE c.user_id = '{user_id}' 
            AND c.bill_date >= '{start_date.strftime('%Y-%m-%d')}' 
            AND c.bill_date <= '{end_date.strftime('%Y-%m-%d')}'
        """
        bills = list(bills_container.query_items(query=bills_query, enable_cross_partition_query=True))
        cash_paid_suppliers = sum(float(bill.get('amount_paid', 0)) for bill in bills)

        net_cash_operating = cash_received - cash_paid_expenses - cash_paid_suppliers

        return jsonify({
            'period': {
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d')
            },
            'operating_activities': {
                'cash_received_from_customers': round(cash_received, 2),
                'cash_paid_for_expenses': round(-cash_paid_expenses, 2),
                'cash_paid_to_suppliers': round(-cash_paid_suppliers, 2),
                'net_cash_from_operating': round(net_cash_operating, 2)
            },
            'investing_activities': {
                'net_cash_from_investing': 0  # Can be extended
            },
            'financing_activities': {
                'net_cash_from_financing': 0  # Can be extended
            },
            'net_increase_in_cash': round(net_cash_operating, 2)
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

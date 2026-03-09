from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container
import uuid
from datetime import datetime
import csv
import io
import re

bank_reconciliation_blueprint = Blueprint('bank_reconciliation', __name__)

# ── Containers ────────────────────────────────────────────────────────────────
bank_txns_container    = get_container("bank_transactions", "/user_id")
invoices_container     = get_container("invoices", "/customer_id")
expenses_container     = get_container("expenses", "/id")

# ── Auth helper (X-User-Id pattern) ─────────────────────────────────────────
def get_user_id():
    uid = request.headers.get('X-User-Id')
    return uid  # None → caller returns 401


# ─────────────────────────────────────────────────────────────────────────────
# CSV PARSER
# Accepts common bank export formats:
#   date, description, debit, credit, balance   (Debit/Credit columns)
#   date, description, amount, balance           (signed amount column)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_csv(file_text):
    """Return list of {date, description, amount, raw_row} or raise ValueError."""
    reader = csv.DictReader(io.StringIO(file_text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]

    transactions = []
    for row in reader:
        row_clean = {k.strip().lower(): (v or '').strip() for k, v in row.items()}

        # ── Resolve date ──────────────────────────────────────────────────
        date_val = (
            row_clean.get('date') or row_clean.get('transaction date') or
            row_clean.get('value date') or row_clean.get('posting date') or ''
        )
        # Normalise common formats → YYYY-MM-DD
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d',
                    '%d %b %Y', '%d %B %Y', '%b %d %Y'):
            try:
                date_val = datetime.strptime(date_val, fmt).strftime('%Y-%m-%d')
                break
            except ValueError:
                pass

        # ── Resolve amount ────────────────────────────────────────────────
        amount = 0.0
        if 'amount' in row_clean:
            try:
                amount = float(re.sub(r'[^\d.\-]', '', row_clean['amount']))
            except ValueError:
                pass
        else:
            debit  = float(re.sub(r'[^\d.]', '', row_clean.get('debit', '') or '0') or 0)
            credit = float(re.sub(r'[^\d.]', '', row_clean.get('credit', '') or '0') or 0)
            amount = credit - debit   # positive = money in, negative = money out

        # ── Resolve description ───────────────────────────────────────────
        desc = (
            row_clean.get('description') or row_clean.get('narration') or
            row_clean.get('particulars') or row_clean.get('details') or
            row_clean.get('transaction details') or ''
        )

        transactions.append({
            'date': date_val,
            'description': desc,
            'amount': round(amount, 2),
        })

    return transactions


# ─────────────────────────────────────────────────────────────────────────────
# QIF PARSER  (!Type:Bank sections with D/T/P/M/^ records)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_qif(text):
    transactions = []
    current = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('!'):
            continue
        if line == '^':
            if current:
                # Normalise date
                raw_date = current.get('date', '')
                for fmt in ('%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d', '%m/%d/%y', '%d/%m/%y'):
                    try:
                        raw_date = datetime.strptime(raw_date, fmt).strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        pass
                transactions.append({
                    'date': raw_date,
                    'description': current.get('payee', current.get('memo', '')),
                    'amount': round(float(current.get('amount', 0)), 2),
                })
            current = {}
        elif line[0] == 'D':
            current['date'] = line[1:]
        elif line[0] == 'T':
            try:
                current['amount'] = float(re.sub(r'[^\d.\-]', '', line[1:]))
            except ValueError:
                current['amount'] = 0.0
        elif line[0] == 'P':
            current['payee'] = line[1:]
        elif line[0] == 'M':
            current['memo'] = line[1:]
    return transactions


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-MATCH LOGIC
# ─────────────────────────────────────────────────────────────────────────────
def _auto_match(txn, user_id):
    """Try to match a bank transaction to an unpaid invoice or expense."""
    amt = abs(txn['amount'])
    if amt == 0:
        return None, None

    # Match against unpaid invoices (balance_due within 1%)
    try:
        inv_query = (
            f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id "
            f"FROM c WHERE c.user_id = '{user_id}' "
            f"AND c.status IN ('Issued','Overdue') "
            f"AND c.balance_due > 0"
        )
        invoices = list(invoices_container.query_items(
            query=inv_query, enable_cross_partition_query=True
        ))
        for inv in invoices:
            due = float(inv.get('balance_due', 0))
            if due > 0 and abs(due - amt) / max(due, amt) <= 0.01:
                return 'invoice', inv['id']
    except Exception:
        pass

    # Match against expenses by amount (within 1%)
    try:
        exp_query = (
            f"SELECT c.id, c.vendor_name, c.amount "
            f"FROM c WHERE 1=1"
        )
        exps = list(expenses_container.query_items(
            query=exp_query, enable_cross_partition_query=True
        ))
        for exp in exps:
            exp_amt = float(exp.get('amount', 0))
            if exp_amt > 0 and abs(exp_amt - amt) / max(exp_amt, amt) <= 0.01:
                return 'expense', exp['id']
    except Exception:
        pass

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD & PARSE BANK STATEMENT
# POST /api/reconciliation/upload
# Accepts: multipart/form-data with 'file' (CSV or QIF) + 'bank_account_id'
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/upload', methods=['POST'])
def upload_statement():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('csv', 'qif'):
        return jsonify({'error': 'Only CSV and QIF files are supported'}), 400

    bank_account_id = request.form.get('bank_account_id', '')
    text = file.read().decode('utf-8', errors='replace')

    # Parse
    try:
        raw_txns = _parse_csv(text) if ext == 'csv' else _parse_qif(text)
    except Exception as e:
        return jsonify({'error': f'Parse error: {str(e)}'}), 422

    if not raw_txns:
        return jsonify({'error': 'No transactions found in file'}), 422

    # Persist each transaction (skip duplicates by date+amount+desc hash)
    saved, skipped = [], 0
    now = datetime.utcnow().isoformat() + 'Z'

    for t in raw_txns:
        # Auto-match attempt
        match_type, match_id = _auto_match(t, user_id)

        doc = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'bank_account_id': bank_account_id,
            'date': t['date'],
            'description': t['description'],
            'amount': t['amount'],
            'match_status': 'matched' if match_id else 'unmatched',
            'match_type': match_type,     # 'invoice' | 'expense' | None
            'match_id': match_id,          # matched record id or None
            'created_at': now,
            'updated_at': now,
        }
        bank_txns_container.create_item(body=doc)
        saved.append(doc)

    return jsonify({
        'imported': len(saved),
        'skipped': skipped,
        'transactions': saved,
    }), 201


# ─────────────────────────────────────────────────────────────────────────────
# LIST TRANSACTIONS
# GET /api/reconciliation/transactions?bank_account_id=&status=
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/transactions', methods=['GET'])
def list_transactions():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    bank_account_id = request.args.get('bank_account_id')
    status_filter   = request.args.get('status')    # matched | unmatched | excluded

    query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
    if bank_account_id:
        query += f" AND c.bank_account_id = '{bank_account_id}'"
    if status_filter:
        query += f" AND c.match_status = '{status_filter}'"
    query += " ORDER BY c.date DESC"

    try:
        items = list(bank_txns_container.query_items(
            query=query, enable_cross_partition_query=True
        ))
        return jsonify(items), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# MANUAL MATCH
# POST /api/reconciliation/<txn_id>/match
# Body: { match_type: 'invoice'|'expense', match_id: '<id>' }
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/<txn_id>/match', methods=['POST'])
def match_transaction(txn_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    data = request.get_json() or {}
    match_type = data.get('match_type')   # 'invoice' | 'expense'
    match_id   = data.get('match_id')

    if not match_type or not match_id:
        return jsonify({'error': 'match_type and match_id are required'}), 400

    try:
        items = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.id = '{txn_id}' AND c.user_id = '{user_id}'",
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404

        txn = items[0]
        txn['match_status'] = 'matched'
        txn['match_type']   = match_type
        txn['match_id']     = match_id
        txn['updated_at']   = datetime.utcnow().isoformat() + 'Z'

        bank_txns_container.replace_item(item=txn_id, body=txn)
        return jsonify(txn), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# UNMATCH
# POST /api/reconciliation/<txn_id>/unmatch
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/<txn_id>/unmatch', methods=['POST'])
def unmatch_transaction(txn_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    try:
        items = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.id = '{txn_id}' AND c.user_id = '{user_id}'",
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404

        txn = items[0]
        txn['match_status'] = 'unmatched'
        txn['match_type']   = None
        txn['match_id']     = None
        txn['updated_at']   = datetime.utcnow().isoformat() + 'Z'

        bank_txns_container.replace_item(item=txn_id, body=txn)
        return jsonify(txn), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# CREATE EXPENSE FROM TRANSACTION
# POST /api/reconciliation/<txn_id>/create-expense
# Body: { category, vendor_name, notes }
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/<txn_id>/create-expense', methods=['POST'])
def create_expense_from_txn(txn_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    data = request.get_json() or {}

    try:
        items = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.id = '{txn_id}' AND c.user_id = '{user_id}'",
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404

        txn = items[0]
        now = datetime.utcnow().isoformat() + 'Z'

        expense_id = str(uuid.uuid4())
        expense = {
            'id': expense_id,
            'vendor_name': data.get('vendor_name', txn.get('description', 'Unknown')),
            'date': txn['date'],
            'category': data.get('category', 'Uncategorized'),
            'amount': abs(txn['amount']),
            'currency': data.get('currency', 'INR'),
            'notes': data.get('notes', f"Imported from bank statement: {txn.get('description', '')}"),
            'source': 'bank_import',
            'bank_txn_id': txn_id,
            'created_at': now,
            'updated_at': now,
        }
        expenses_container.create_item(body=expense)

        # Mark transaction as matched
        txn['match_status'] = 'matched'
        txn['match_type']   = 'expense'
        txn['match_id']     = expense_id
        txn['updated_at']   = now
        bank_txns_container.replace_item(item=txn_id, body=txn)

        return jsonify({'expense': expense, 'transaction': txn}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-MATCH ALL UNMATCHED  (re-run on demand)
# POST /api/reconciliation/auto-match
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/auto-match', methods=['POST'])
def run_auto_match():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    try:
        unmatched = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.user_id = '{user_id}' AND c.match_status = 'unmatched'",
            enable_cross_partition_query=True
        ))

        matched_count = 0
        now = datetime.utcnow().isoformat() + 'Z'

        for txn in unmatched:
            match_type, match_id = _auto_match(txn, user_id)
            if match_id:
                txn['match_status'] = 'matched'
                txn['match_type']   = match_type
                txn['match_id']     = match_id
                txn['updated_at']   = now
                bank_txns_container.replace_item(item=txn['id'], body=txn)
                matched_count += 1

        return jsonify({
            'processed': len(unmatched),
            'newly_matched': matched_count,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# DELETE TRANSACTION
# DELETE /api/reconciliation/<txn_id>
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/<txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    try:
        items = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.id = '{txn_id}' AND c.user_id = '{user_id}'",
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404

        bank_txns_container.delete_item(item=txn_id, partition_key=user_id)
        return jsonify({'message': 'Transaction deleted'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET MATCHABLE RECORDS  (for manual-match dialog)
# GET /api/reconciliation/matchable?type=invoice|expense&q=<search>
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/matchable', methods=['GET'])
def get_matchable():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'X-User-Id header is required'}), 401

    rec_type = request.args.get('type', 'invoice')  # 'invoice' | 'expense'
    search   = request.args.get('q', '').lower()

    try:
        if rec_type == 'invoice':
            items = list(invoices_container.query_items(
                query=(
                    f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id, c.status "
                    f"FROM c WHERE c.user_id = '{user_id}' AND c.status IN ('Issued','Overdue') AND c.balance_due > 0"
                ),
                enable_cross_partition_query=True
            ))
            if search:
                items = [i for i in items if search in (i.get('invoice_number', '') or '').lower()]
        else:
            items = list(expenses_container.query_items(
                query="SELECT c.id, c.vendor_name, c.amount, c.date, c.category FROM c WHERE 1=1",
                enable_cross_partition_query=True
            ))
            if search:
                items = [i for i in items if search in (i.get('vendor_name', '') or '').lower()]

        return jsonify(items[:50]), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

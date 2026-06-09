from flask import Blueprint, request, jsonify
from smart_invoice_pro.services.bank_import.import_workflow_service import (
    create_import_batch,
    delete_batch,
    get_batch,
    get_job,
    list_batches,
    list_rows,
    mark_batch_approved,
    update_row,
)
from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.cosmos_client import get_container
from smart_invoice_pro.utils.domain_events import record_domain_event
from smart_invoice_pro.utils.notifications import create_notification
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

# ── Auth helper (uses JWT context from middleware) ──────────────────────────
def get_user_id():
    uid = getattr(request, 'user_id', None)
    return uid  # None -> caller returns 401


def get_tenant_id():
    return getattr(request, 'tenant_id', None)


def _require_actor():
    user_id = get_user_id()
    tenant_id = get_tenant_id()
    if not user_id or not tenant_id:
        return None, None, (jsonify({'error': 'Unauthorized'}), 401)
    return user_id, tenant_id, None


def _tenant_legacy_clause(tenant_id):
    if not tenant_id:
        return ''
    return f" AND (NOT IS_DEFINED(c.tenant_id) OR c.tenant_id = '{tenant_id}')"


def _txn_lookup_query(txn_id, user_id, tenant_id):
    return (
        f"SELECT * FROM c WHERE c.id = '{txn_id}' AND c.user_id = '{user_id}'"
        f"{_tenant_legacy_clause(tenant_id)}"
    )


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
def _auto_match(txn, user_id, tenant_id=None):
    """Try to match a bank transaction to an unpaid invoice or expense."""
    amt = abs(txn['amount'])
    if amt == 0:
        return None, None

    # Match against unpaid invoices (balance_due within 1%)
    try:
        inv_query = f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id FROM c WHERE c.user_id = '{user_id}' "
        if tenant_id:
            inv_query += f"AND c.tenant_id = '{tenant_id}' "
        inv_query += "AND c.status IN ('Issued','Overdue') AND c.balance_due > 0"
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
        exp_query = "SELECT c.id, c.vendor_name, c.amount FROM c WHERE 1=1 "
        if tenant_id:
            exp_query += f"AND c.tenant_id = '{tenant_id}' "
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


def _persist_approved_bank_transaction(row_doc, user_id, tenant_id):
    now = datetime.utcnow().isoformat() + 'Z'
    match_type, match_id = _auto_match(
        {
            'amount': row_doc.get('amount', 0),
            'description': row_doc.get('description', ''),
            'date': row_doc.get('normalized_date', ''),
        },
        user_id,
        tenant_id,
    )
    txn_doc = {
        'id': str(uuid.uuid4()),
        'user_id': user_id,
        'tenant_id': tenant_id,
        'bank_account_id': row_doc.get('bank_account_id'),
        'date': row_doc.get('normalized_date'),
        'description': row_doc.get('description'),
        'amount': row_doc.get('amount'),
        'currency': row_doc.get('currency', 'INR'),
        'import_batch_id': row_doc.get('batch_id'),
        'import_row_id': row_doc.get('id'),
        'source': 'bank_import_review',
        'match_status': 'matched' if match_id else 'unmatched',
        'match_type': match_type,
        'match_id': match_id,
        'created_at': now,
        'updated_at': now,
    }
    bank_txns_container.create_item(body=txn_doc)
    return txn_doc


@bank_reconciliation_blueprint.route('/reconciliation/import-batches', methods=['POST'])
def create_statement_import_batch():
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    upload = request.files['file']
    if not upload.filename:
        return jsonify({'error': 'Empty filename'}), 400

    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({'error': 'Empty file'}), 400

    max_bytes = 10 * 1024 * 1024
    if len(file_bytes) > max_bytes:
        return jsonify({'error': 'File exceeds maximum size of 10 MB'}), 400

    from smart_invoice_pro.services.bank_import.import_workflow_service import detect_file_profile
    profile = detect_file_profile(upload.filename, upload.content_type)
    if not profile.get('supported'):
        return jsonify({'error': 'Unsupported or invalid bank statement file type'}), 400

    bank_account_id = (request.form.get('bank_account_id') or '').strip()
    pdf_password = (request.form.get('pdf_password') or request.form.get('file_password') or '').strip()

    try:
        batch_doc, job_doc, row_docs = create_import_batch(
            tenant_id=tenant_id,
            user_id=user_id,
            bank_account_id=bank_account_id,
            filename=upload.filename,
            content_type=upload.content_type or 'application/octet-stream',
            file_bytes=file_bytes,
            pdf_password=pdf_password,
        )
    except ValueError as exc:
        err_str = str(exc)
        if err_str.startswith('PDF_PASSWORD_REQUIRED'):
            return jsonify({
                'error': 'This PDF is password-protected. Please provide the password.',
                'error_code': 'FILE_PASSWORD_REQUIRED',
            }), 400
        if err_str.startswith('EXCEL_PASSWORD_REQUIRED'):
            return jsonify({
                'error': 'This Excel file is password-protected. Please provide the password.',
                'error_code': 'FILE_PASSWORD_REQUIRED',
            }), 400
        return jsonify({'error': err_str}), 400
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    log_audit_event(
        {
            'action': 'BANK_IMPORT_BATCH_CREATED',
            'entity': 'bank_import_batch',
            'entity_id': batch_doc['id'],
            'after': {
                'filename': batch_doc.get('filename'),
                'row_count': batch_doc.get('row_count'),
                'status': batch_doc.get('status'),
            },
            'metadata': {
                'job_id': job_doc.get('id'),
                'workflow_mode': batch_doc.get('workflow_mode'),
                'job_status': job_doc.get('status'),
            },
        }
    )
    record_domain_event(
        'BANK_IMPORT_BATCH_CREATED',
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type='bank_import_batch',
        entity_id=batch_doc['id'],
        payload={'job_id': job_doc['id'], 'row_count': batch_doc.get('row_count', 0)},
    )
    create_notification(
        tenant_id,
        'bank_import_started' if job_doc.get('status') != 'completed' else 'bank_import_ready',
        'Bank import processing started' if job_doc.get('status') != 'completed' else 'Bank import ready for review',
        (
            f"{batch_doc['filename']} has been queued for processing."
            if job_doc.get('status') != 'completed'
            else f"{batch_doc['filename']} is ready for review."
        ),
        entity_id=batch_doc['id'],
        entity_type='bank_import_batch',
        user_id=user_id,
    )
    return jsonify({'batch': batch_doc, 'job': job_doc, 'rows': row_docs}), 201


@bank_reconciliation_blueprint.route('/reconciliation/import-batches', methods=['GET'])
def list_statement_import_batches():
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    bank_account_id = request.args.get('bank_account_id') or None
    try:
        return jsonify(list_batches(tenant_id=tenant_id, bank_account_id=bank_account_id)), 200
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bank_reconciliation_blueprint.route('/reconciliation/import-batches/<batch_id>', methods=['GET'])
def get_statement_import_batch(batch_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    batch_doc = get_batch(tenant_id=tenant_id, batch_id=batch_id)
    if not batch_doc:
        return jsonify({'error': 'Import batch not found'}), 404
    return jsonify(batch_doc), 200


@bank_reconciliation_blueprint.route('/reconciliation/import-batches/<batch_id>', methods=['DELETE'])
def delete_statement_import_batch(batch_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    try:
        deleted = delete_batch(tenant_id=tenant_id, batch_id=batch_id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    if not deleted:
        return jsonify({'error': 'Import batch not found'}), 404
    return jsonify({'deleted': True, 'batch_id': batch_id}), 200


@bank_reconciliation_blueprint.route('/reconciliation/import-jobs/<job_id>', methods=['GET'])
def get_statement_import_job(job_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    job_doc = get_job(tenant_id=tenant_id, job_id=job_id)
    if not job_doc:
        return jsonify({'error': 'Import job not found'}), 404
    return jsonify(job_doc), 200


@bank_reconciliation_blueprint.route('/reconciliation/import-batches/<batch_id>/rows', methods=['GET'])
def get_statement_import_rows(batch_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    if not get_batch(tenant_id=tenant_id, batch_id=batch_id):
        return jsonify({'error': 'Import batch not found'}), 404
    return jsonify(list_rows(tenant_id=tenant_id, batch_id=batch_id)), 200


@bank_reconciliation_blueprint.route('/reconciliation/import-batches/<batch_id>/rows/<row_id>', methods=['PATCH'])
def update_statement_import_row(batch_id, row_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    updated_doc = update_row(
        tenant_id=tenant_id,
        batch_id=batch_id,
        row_id=row_id,
        updates=request.get_json() or {},
    )
    if not updated_doc:
        return jsonify({'error': 'Import row not found'}), 404

    log_audit_event(
        {
            'action': 'BANK_IMPORT_ROW_UPDATED',
            'entity': 'bank_import_row',
            'entity_id': row_id,
            'after': updated_doc,
            'metadata': {'batch_id': batch_id},
        }
    )
    return jsonify(updated_doc), 200


@bank_reconciliation_blueprint.route('/reconciliation/import-batches/<batch_id>/approve', methods=['POST'])
def approve_statement_import_batch(batch_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    batch_doc = get_batch(tenant_id=tenant_id, batch_id=batch_id)
    if not batch_doc:
        return jsonify({'error': 'Import batch not found'}), 404

    row_docs = list_rows(tenant_id=tenant_id, batch_id=batch_id)
    approved_rows = [row for row in row_docs if row.get('review_status') != 'rejected']
    created_txns = [_persist_approved_bank_transaction(row, user_id, tenant_id) for row in approved_rows]
    batch_doc = mark_batch_approved(tenant_id=tenant_id, batch_id=batch_id, approved_row_count=len(created_txns))

    log_audit_event(
        {
            'action': 'BANK_IMPORT_BATCH_APPROVED',
            'entity': 'bank_import_batch',
            'entity_id': batch_id,
            'after': {
                'approved_row_count': len(created_txns),
                'status': batch_doc.get('status'),
            },
        }
    )
    record_domain_event(
        'BANK_IMPORT_BATCH_APPROVED',
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type='bank_import_batch',
        entity_id=batch_id,
        payload={'approved_row_count': len(created_txns)},
    )
    create_notification(
        tenant_id,
        'bank_import_approved',
        'Bank import approved',
        f"Import batch {batch_doc.get('filename', batch_id)} is ready in reconciliation.",
        entity_id=batch_id,
        entity_type='bank_import_batch',
        user_id=user_id,
    )
    return jsonify({'batch': batch_doc, 'transactions_created': len(created_txns), 'transactions': created_txns}), 200


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD & PARSE BANK STATEMENT
# POST /api/reconciliation/upload
# Accepts: multipart/form-data with 'file' (CSV or QIF) + 'bank_account_id'
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/upload', methods=['POST'])
def upload_statement():
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('csv', 'qif'):
        return jsonify({'error': 'Only CSV and QIF files are supported'}), 400

    raw = file.read()
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({'error': 'File exceeds maximum size of 10 MB'}), 400
    if ext == 'csv' and raw[:512].count(0) > 2:
        return jsonify({'error': 'File does not appear to be a valid text-based CSV'}), 400

    bank_account_id = request.form.get('bank_account_id', '')
    text = raw.decode('utf-8', errors='replace')

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
        match_type, match_id = _auto_match(t, user_id, tenant_id)

        doc = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'tenant_id': tenant_id,
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    bank_account_id = request.args.get('bank_account_id')
    status_filter   = request.args.get('status')    # matched | unmatched | excluded

    query = f"SELECT * FROM c WHERE c.user_id = '{user_id}'{_tenant_legacy_clause(tenant_id)}"
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    data = request.get_json() or {}
    match_type = data.get('match_type')   # 'invoice' | 'expense'
    match_id   = data.get('match_id')

    if not match_type or not match_id:
        return jsonify({'error': 'match_type and match_id are required'}), 400

    try:
        items = list(bank_txns_container.query_items(
            query=_txn_lookup_query(txn_id, user_id, tenant_id),
            enable_cross_partition_query=True
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404

        txn = items[0]
        if txn.get('match_status') == 'matched' and txn.get('match_id'):
            return jsonify({
                'error': 'Transaction is already matched',
                'match_id': txn.get('match_id'),
            }), 409

        now = datetime.utcnow().isoformat() + 'Z'
        txn['match_status'] = 'matched'
        txn['match_type']   = match_type
        txn['match_id']     = match_id
        txn['matched_at']   = now
        txn['updated_at']   = now

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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    try:
        items = list(bank_txns_container.query_items(
            query=_txn_lookup_query(txn_id, user_id, tenant_id),
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    data = request.get_json() or {}

    try:
        items = list(bank_txns_container.query_items(
            query=_txn_lookup_query(txn_id, user_id, tenant_id),
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    try:
        unmatched = list(bank_txns_container.query_items(
            query=f"SELECT * FROM c WHERE c.user_id = '{user_id}'{_tenant_legacy_clause(tenant_id)} AND c.match_status = 'unmatched'",
            enable_cross_partition_query=True
        ))

        matched_count = 0
        now = datetime.utcnow().isoformat() + 'Z'

        for txn in unmatched:
            match_type, match_id = _auto_match(txn, user_id, tenant_id)
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    try:
        items = list(bank_txns_container.query_items(
            query=_txn_lookup_query(txn_id, user_id, tenant_id),
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
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    rec_type = request.args.get('type', 'invoice')  # 'invoice' | 'expense'
    search   = request.args.get('q', '').lower()

    try:
        if rec_type == 'invoice':
            query = (
                f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id, c.status "
                f"FROM c WHERE c.user_id = '{user_id}' "
            )
            if tenant_id:
                query += f"AND c.tenant_id = '{tenant_id}' "
            query += "AND c.status IN ('Issued','Overdue') AND c.balance_due > 0"
            items = list(invoices_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            if search:
                items = [i for i in items if search in (i.get('invoice_number', '') or '').lower()]
        else:
            query = "SELECT c.id, c.vendor_name, c.amount, c.date, c.category FROM c WHERE 1=1 "
            if tenant_id:
                query += f"AND c.tenant_id = '{tenant_id}' "
            items = list(expenses_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            if search:
                items = [i for i in items if search in (i.get('vendor_name', '') or '').lower()]

        return jsonify(items[:50]), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# AI SUGGEST MATCH  (Claude-powered, per-transaction)
# POST /api/reconciliation/<txn_id>/ai-suggest
# Returns a ranked suggestion with confidence + reasoning.
# Does NOT apply the match — the user confirms via the manual-match flow.
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/<txn_id>/ai-suggest', methods=['POST'])
def ai_suggest_match(txn_id):
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    try:
        items = list(bank_txns_container.query_items(
            query=_txn_lookup_query(txn_id, user_id, tenant_id),
            enable_cross_partition_query=True,
        ))
        if not items:
            return jsonify({'error': 'Transaction not found'}), 404
        txn = items[0]

        # Fetch candidate invoices
        inv_query = (
            f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id, c.customer_name, c.due_date "
            f"FROM c WHERE c.user_id = '{user_id}' "
        )
        if tenant_id:
            inv_query += f"AND c.tenant_id = '{tenant_id}' "
        inv_query += "AND c.status IN ('Issued','Overdue') AND c.balance_due > 0"
        candidates_invoices = list(invoices_container.query_items(
            query=inv_query, enable_cross_partition_query=True,
        ))

        # Fetch candidate expenses
        exp_query = "SELECT c.id, c.vendor_name, c.amount, c.date, c.category FROM c WHERE 1=1 "
        if tenant_id:
            exp_query += f"AND c.tenant_id = '{tenant_id}' "
        candidates_expenses = list(expenses_container.query_items(
            query=exp_query, enable_cross_partition_query=True,
        ))

        from smart_invoice_pro.services.ai_reconciliation_service import ai_match_transaction
        suggestion = ai_match_transaction(txn, candidates_invoices, candidates_expenses)

        return jsonify({'transaction_id': txn_id, 'suggestion': suggestion}), 200

    except RuntimeError as e:
        # ANTHROPIC_API_KEY not configured
        return jsonify({'error': str(e)}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# AI AUTO-MATCH ALL UNMATCHED  (Claude-powered bulk run)
# POST /api/reconciliation/ai-match
# Body (optional): { "confidence_threshold": 0.85 }
# Fetches all unmatched transactions, asks Claude for each, and auto-applies
# matches whose confidence meets the threshold.
# ─────────────────────────────────────────────────────────────────────────────
@bank_reconciliation_blueprint.route('/reconciliation/ai-match', methods=['POST'])
def run_ai_match():
    user_id, tenant_id, error_response = _require_actor()
    if error_response:
        return error_response

    body = request.get_json(silent=True) or {}
    confidence_threshold = float(body.get('confidence_threshold', 0.85))

    try:
        from smart_invoice_pro.services.ai_reconciliation_service import ai_match_transaction

        unmatched = list(bank_txns_container.query_items(
            query=(
                f"SELECT * FROM c WHERE c.user_id = '{user_id}'"
                f"{_tenant_legacy_clause(tenant_id)}"
                f" AND c.match_status = 'unmatched'"
            ),
            enable_cross_partition_query=True,
        ))

        # Fetch all candidates once to avoid N+1 API calls to Cosmos
        inv_query = (
            f"SELECT c.id, c.invoice_number, c.balance_due, c.customer_id, c.customer_name, c.due_date "
            f"FROM c WHERE c.user_id = '{user_id}' "
        )
        if tenant_id:
            inv_query += f"AND c.tenant_id = '{tenant_id}' "
        inv_query += "AND c.status IN ('Issued','Overdue') AND c.balance_due > 0"
        candidates_invoices = list(invoices_container.query_items(
            query=inv_query, enable_cross_partition_query=True,
        ))

        exp_query = "SELECT c.id, c.vendor_name, c.amount, c.date, c.category FROM c WHERE 1=1 "
        if tenant_id:
            exp_query += f"AND c.tenant_id = '{tenant_id}' "
        candidates_expenses = list(expenses_container.query_items(
            query=exp_query, enable_cross_partition_query=True,
        ))

        matched_count = 0
        results = []
        now = datetime.utcnow().isoformat() + 'Z'

        for txn in unmatched:
            try:
                suggestion = ai_match_transaction(txn, candidates_invoices, candidates_expenses)
            except RuntimeError:
                raise  # propagate API key / config errors to the outer handler
            except Exception:
                continue  # skip individual per-transaction failures

            will_apply = (
                suggestion.get('match_id') is not None
                and suggestion.get('confidence', 0) >= confidence_threshold
            )

            if will_apply:
                txn['match_status'] = 'matched'
                txn['match_type'] = suggestion['match_type']
                txn['match_id'] = suggestion['match_id']
                txn['match_confidence'] = suggestion['confidence']
                txn['match_reasoning'] = suggestion.get('reasoning', '')
                txn['updated_at'] = now
                bank_txns_container.replace_item(item=txn['id'], body=txn)
                matched_count += 1

            results.append({
                'transaction_id': txn['id'],
                'suggestion': suggestion,
                'applied': will_apply,
            })

        return jsonify({
            'processed': len(unmatched),
            'newly_matched': matched_count,
            'confidence_threshold': confidence_threshold,
            'results': results,
        }), 200

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500

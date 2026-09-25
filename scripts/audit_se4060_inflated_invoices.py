#!/usr/bin/env python3
"""
audit_se4060_inflated_invoices.py
=================================
READ-ONLY audit of saved invoices. It never writes to the database; it only
queries and prints CSV. Correcting invoices is a separate decision.

Reports
-------
  se4060    Invoices whose saved tax was inflated by the SE-4060 bug: the stored
            CGST/SGST/IGST split was added on top of the line tax (on update, or
            in the create fallback). Expected tax = _resolve_total_tax() over
            _compute_item_totals(), as on develop after PR #62.
  zero-gst  Invoices created before PR #60 (0d1699f, 23 Sep 2026 13:47 IST)
            for 'consumer' or 'composition' customers that were saved with zero
            GST although the seller is not a composition/unregistered org.
            Expected tax = what calculate_gst() charges on current develop.

Usage
-----
  cd smart-invoice-pro-api-2
  python scripts/audit_se4060_inflated_invoices.py [--report se4060|zero-gst|both]
      [--since YYYY-MM-DD] [--tenant <id>] [--limit N] [--before <date or ISO time>]
      [--out ~/Desktop/se4060-audit.csv]

With --out and --report both, two files are written: <name>-se4060.csv and
<name>-zero-gst.csv. Without --out, CSV goes to stdout and the summary to stderr.

Environment variables required (same as main app):
  COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME

The CSV holds customer data: keep it outside the repo and do not commit it.
"""
import argparse
import csv
import itertools
import os
import sys
from datetime import datetime, timedelta, timezone

# ── Allow running from the repo root ────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

TOLERANCE = 0.01

# PR #60 merged into develop at 2026-09-23 13:47 IST; created_at is stored as naive UTC.
PR60_CUTOFF_UTC = datetime(2026, 9, 23, 8, 17)

ZERO_GST_TREATMENTS = {'consumer', 'composition'}

FIELDS = [
    'invoice_id', 'invoice_number', 'tenant_id', 'user_id', 'customer_name', 'invoice_date', 'status',
    'stored_total_tax', 'expected_total_tax', 'stored_total_amount', 'expected_total_amount',
    'difference', 'amount_paid', 'balance_due', 'reason',
]
ZERO_GST_FIELDS = FIELDS[:-1] + ['gst_treatment', 'reason']


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _row(inv: dict, expected_tax: float, reason: str) -> dict:
    stored_tax = _num(inv.get('total_tax'))
    stored_total = _num(inv.get('total_amount'))
    difference = stored_tax - expected_tax
    return {
        'invoice_id': inv.get('id', ''),
        'invoice_number': inv.get('invoice_number', ''),
        'tenant_id': inv.get('tenant_id', ''),
        'user_id': inv.get('user_id', ''),
        'customer_name': inv.get('customer_name', ''),
        'invoice_date': inv.get('issue_date', ''),
        'status': inv.get('status', ''),
        'stored_total_tax': round(stored_tax, 2),
        'expected_total_tax': round(expected_tax, 2),
        'stored_total_amount': round(stored_total, 2),
        'expected_total_amount': round(stored_total - difference, 2),
        'difference': round(difference, 2),
        'amount_paid': round(_num(inv.get('amount_paid')), 2),
        'balance_due': round(_num(inv.get('balance_due')), 2),
        'reason': reason,
    }


# ── Report 1: SE-4060 inflated totals ───────────────────────────────────────

def find_inflated(invoices):
    """Rows for invoices whose stored tax = line tax + stored CGST/SGST/IGST split."""
    from smart_invoice_pro.api.invoices import _compute_item_totals, _resolve_total_tax

    rows = []
    for inv in invoices:
        if not inv.get('is_gst_applicable'):
            continue
        _, _, item_tax = _compute_item_totals(inv.get('items') or [], is_gst_applicable=True)
        split = _num(inv.get('cgst_amount')) + _num(inv.get('sgst_amount')) + _num(inv.get('igst_amount'))
        if item_tax <= 0 or split <= 0:
            continue
        stored_tax = _num(inv.get('total_tax'))
        if abs(stored_tax - (item_tax + split)) > TOLERANCE:
            continue
        expected_tax = _resolve_total_tax(item_tax, split, True)
        if stored_tax - expected_tax > TOLERANCE:
            rows.append(_row(inv, expected_tax, 'line tax + stored split'))
    return rows


# ── Report 2: zero GST for consumer/composition customers before PR #60 ─────

def _created_before(inv: dict, cutoff: datetime) -> bool:
    created = inv.get('created_at')
    if created:
        try:
            parsed = datetime.fromisoformat(str(created).replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed < cutoff
        except ValueError:
            pass
    issue_date = str(inv.get('issue_date') or '')
    return bool(issue_date) and issue_date < cutoff.strftime('%Y-%m-%d')


def find_zero_gst(invoices, cutoff, seller_suppresses_tax, seller_state, customer_info):
    """
    Rows for zero-tax consumer/composition invoices created before `cutoff`.

    seller_suppresses_tax(tenant_id) -> bool, seller_state(tenant_id) -> str and
    customer_info(tenant_id, customer_id) -> (state, gst_treatment, place_of_supply)
    are read-only lookups, injected so tests need no database.
    """
    from smart_invoice_pro.api.tax_rates_api import calculate_gst

    rows = []
    for inv in invoices:
        if not inv.get('is_gst_applicable'):
            continue
        if abs(_num(inv.get('total_tax'))) > TOLERANCE:
            continue
        if not _created_before(inv, cutoff):
            continue
        tenant_id = inv.get('tenant_id', '')
        treatment = (inv.get('gst_treatment') or '').strip().lower()
        customer_state, customer_treatment, customer_pos = '', '', ''
        if not treatment or not inv.get('place_of_supply'):
            customer_state, customer_treatment, customer_pos = customer_info(
                tenant_id, str(inv.get('customer_id', ''))
            )
        treatment = treatment or (customer_treatment or '').strip().lower()
        if treatment not in ZERO_GST_TREATMENTS:
            continue
        if seller_suppresses_tax(tenant_id):
            continue
        result = calculate_gst(
            items=inv.get('items') or [],
            seller_state=seller_state(tenant_id),
            customer_state=customer_state,
            gst_treatment=treatment,
            is_gst_applicable=True,
            place_of_supply=inv.get('place_of_supply') or customer_pos or customer_state,
        )
        expected_tax = _num(result.get('total_tax'))
        if expected_tax > TOLERANCE:
            row = _row(inv, expected_tax, 'zero GST, pre-PR #60 treatment')
            row['gst_treatment'] = treatment
            rows.append(row)
    return rows


# ── Database access (queries only) ──────────────────────────────────────────

def query_invoices(container, since=None, tenant=None, limit=None):
    clauses, params = [], []
    if since:
        clauses.append('c.issue_date >= @since')
        params.append({'name': '@since', 'value': since})
    if tenant:
        clauses.append('c.tenant_id = @tid')
        params.append({'name': '@tid', 'value': tenant})
    query = 'SELECT * FROM c' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
    items = container.query_items(query=query, parameters=params, enable_cross_partition_query=True)
    return list(itertools.islice(items, limit) if limit else items)


def _cached(fn):
    cache = {}

    def wrapper(*args):
        if args not in cache:
            cache[args] = fn(*args)
        return cache[args]
    return wrapper


def _parse_cutoff(value: str) -> datetime:
    """YYYY-MM-DD is read as midnight IST; an ISO time without offset as UTC."""
    if len(value) == 10:
        return datetime.strptime(value, '%Y-%m-%d') - timedelta(hours=5, minutes=30)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _write_csv(rows, fields, path, title):
    if path:
        with open(path, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f'{title}: wrote {path}', file=sys.stderr)
    else:
        print(f'# {title}')
        writer = csv.DictWriter(sys.stdout, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        print()


def _out_path(out, suffix, both):
    if not out:
        return None
    out = os.path.expanduser(out)
    if not both:
        return out
    stem, ext = os.path.splitext(out)
    return f'{stem}-{suffix}{ext or ".csv"}'


def _summary(title, rows):
    total = sum(row['difference'] for row in rows)
    label = 'total overstatement' if title == 'se4060' else 'total GST not charged'
    print(f'{title}: {len(rows)} invoice(s), {label} {abs(total):,.2f}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only audit of invoice tax totals (SE-4060).')
    parser.add_argument('--report', choices=['se4060', 'zero-gst', 'both'], default='both')
    parser.add_argument('--since', help='Only invoices with issue_date on or after YYYY-MM-DD.')
    parser.add_argument('--tenant', help='Only this tenant id.')
    parser.add_argument('--limit', type=int, help='Scan at most N invoices.')
    parser.add_argument('--before', help='zero-gst cut-off (default: PR #60 merge, 2026-09-23 13:47 IST).')
    parser.add_argument('--out', help='CSV path (keep it outside the repo and OneDrive).')
    args = parser.parse_args(argv)

    from smart_invoice_pro.utils.cosmos_client import invoices_container
    from smart_invoice_pro.utils.org_tax_mode import must_suppress_sales_tax
    from smart_invoice_pro.api.tax_rates_api import _get_seller_state, _get_customer_state

    invoices = query_invoices(invoices_container, args.since, args.tenant, args.limit)
    print(f'Scanned {len(invoices)} invoice(s).', file=sys.stderr)
    both = args.report == 'both'

    if args.report in ('se4060', 'both'):
        rows = find_inflated(invoices)
        _write_csv(rows, FIELDS, _out_path(args.out, 'se4060', both), 'se4060')
        _summary('se4060', rows)

    if args.report in ('zero-gst', 'both'):
        cutoff = _parse_cutoff(args.before) if args.before else PR60_CUTOFF_UTC
        rows = find_zero_gst(
            invoices, cutoff,
            _cached(must_suppress_sales_tax), _cached(_get_seller_state), _cached(_get_customer_state),
        )
        _write_csv(rows, ZERO_GST_FIELDS, _out_path(args.out, 'zero-gst', both), 'zero-gst')
        _summary('zero-gst', rows)


if __name__ == '__main__':
    main()

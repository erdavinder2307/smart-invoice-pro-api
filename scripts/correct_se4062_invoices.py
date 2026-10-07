#!/usr/bin/env python3
"""
correct_se4062_invoices.py
==========================
Corrects the saved totals of invoices inflated by the SE-4060 bug (the stored
CGST/SGST/IGST split was added on top of the line tax). Only the invoice ids
given with --ids are touched.

DRY RUN by default: prints, per id, before -> after for total_tax,
total_amount and balance_due, and writes nothing. --apply writes only those
three fields (plus an entry in the audit log) and refuses an invoice that:
  - is Paid or Partially Paid, or has any amount paid;
  - no longer matches the SE-4060 audit signature (stored tax = line tax +
    stored split), i.e. it was edited or corrected since the audit;
  - cannot be found.

Zero-GST invoices from the audit's zero-gst report are out of scope: charging
GST on them needs a supplementary invoice or debit note, not a data change.

Usage
-----
  python scripts/correct_se4062_invoices.py --ids <id1,id2,...> [--apply]

Environment variables required (same as main app):
  COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME

The output names invoices and amounts: keep it outside the repo and OneDrive.
"""
import argparse
import importlib.util
import os
import sys

# ── Allow running from the repo root ────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

_AUDIT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'audit_se4060_inflated_invoices.py')
_spec = importlib.util.spec_from_file_location('audit_se4060_inflated_invoices', _AUDIT_PATH)
_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_audit)

PAID_STATUSES = {'paid', 'partially paid'}
CHANGED_FIELDS = ('total_tax', 'total_amount', 'balance_due')
SCRIPT_ACTOR = 'script:correct_se4062_invoices'


class ChangedSinceRead(Exception):
    """The invoice changed between the read and the write (etag mismatch)."""


def plan_correction(inv):
    """
    Return (new_values, refusal). Exactly one of them is None.
    new_values holds the corrected total_tax, total_amount and balance_due.
    """
    status = str(inv.get('status') or '').strip().lower()
    if status in PAID_STATUSES:
        return None, f"status is {inv.get('status')}: handle with the customer (refund / credit note)"
    if _audit._num(inv.get('amount_paid')) > _audit.TOLERANCE:
        return None, 'a payment is recorded: handle with the customer (refund / credit note)'
    rows = _audit.find_inflated([inv])
    if not rows:
        return None, 'no longer matches the SE-4060 audit (edited or corrected since)'
    row = rows[0]
    new_total = row['expected_total_amount']
    return {
        'total_tax': row['expected_total_tax'],
        'total_amount': new_total,
        'balance_due': round(new_total - _audit._num(inv.get('amount_paid')), 2),
    }, None


def _fmt(inv, new_values):
    parts = []
    for field in CHANGED_FIELDS:
        before = round(_audit._num(inv.get(field)), 2)
        parts.append(f'{field} {before:,.2f} -> {new_values[field]:,.2f}')
    return '; '.join(parts)


def correct(ids, fetch_invoice, save_invoice, log_change, tenant_kind, apply=False, out=sys.stdout):
    """
    Plan (and with apply=True, write) the correction for each id.
    fetch_invoice(id) -> dict | None, save_invoice(inv) (raises ChangedSinceRead on an etag
    mismatch), log_change(before, after) -> None or a warning string, and
    tenant_kind(tenant_id) -> str are injected so tests need no database.
    Returns counts per outcome.
    """
    counts = {'corrected': 0, 'would_correct': 0, 'refused': 0, 'not_found': 0}
    for invoice_id in ids:
        inv = fetch_invoice(invoice_id)
        if inv is None:
            counts['not_found'] += 1
            print(f'{invoice_id}: NOT FOUND', file=out)
            continue
        label = f"{invoice_id} ({inv.get('invoice_number', '')}, {inv.get('status', '')}, " \
                f"tenant {tenant_kind(inv.get('tenant_id', ''))})"
        new_values, refusal = plan_correction(inv)
        if refusal:
            counts['refused'] += 1
            print(f'{label}: REFUSED, {refusal}', file=out)
            continue
        if not apply:
            counts['would_correct'] += 1
            print(f'{label}: DRY RUN, {_fmt(inv, new_values)}', file=out)
            continue
        before = dict(inv)
        after = {**inv, **new_values}
        try:
            save_invoice(after)
        except ChangedSinceRead:
            counts['refused'] += 1
            print(f'{label}: REFUSED, changed since read: run the dry run again', file=out)
            continue
        warning = log_change(before, after)
        counts['corrected'] += 1
        print(f'{label}: CORRECTED, {_fmt(inv, new_values)}', file=out)
        if warning:
            print(f'{label}: WARNING, {warning}', file=out)
    return counts


# ── Database access ─────────────────────────────────────────────────────────

def _make_fetch(container):
    def fetch(invoice_id):
        items = list(container.query_items(
            query='SELECT * FROM c WHERE c.id = @id',
            parameters=[{'name': '@id', 'value': invoice_id}],
            enable_cross_partition_query=True,
        ))
        return items[0] if items else None
    return fetch


def _make_save(container):
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosAccessConditionFailedError

    def save(inv):
        # The etag from the read makes Cosmos reject the write if the invoice changed in between.
        try:
            container.replace_item(
                item=inv['id'], body=inv, etag=inv.get('_etag'), match_condition=MatchConditions.IfNotModified,
            )
        except CosmosAccessConditionFailedError as exc:
            raise ChangedSinceRead() from exc
    return save


def _log_change(before, after):
    """Write the audit entry before returning; return a warning string when none was written."""
    from smart_invoice_pro.utils import audit_logger

    if not after.get('tenant_id'):
        return 'the invoice has no tenant_id, so no audit log entry was written'
    # log_audit normally writes on a daemon thread, which Python kills when the script exits;
    # write inline instead so the entry is stored before the next invoice (or exit).
    failed_before = audit_logger.get_audit_write_stats().get('failed', 0)
    background_write = audit_logger._fire_and_forget_write
    audit_logger._fire_and_forget_write = audit_logger._write_audit_doc
    try:
        audit_logger.log_audit(
            'invoice', 'update', after['id'],
            {field: before.get(field) for field in CHANGED_FIELDS},
            {field: after.get(field) for field in CHANGED_FIELDS},
            user_id=SCRIPT_ACTOR, tenant_id=after.get('tenant_id'),
            summary='SE-4062: corrected totals inflated by the SE-4060 bug',
        )
    finally:
        audit_logger._fire_and_forget_write = background_write
    if audit_logger.get_audit_write_stats().get('failed', 0) > failed_before:
        return 'the audit log write failed (see the warning above); record this correction by hand'
    return None


def _tenant_kind(tenant_id):
    from smart_invoice_pro.utils.tenant_service import get_tenant_by_id

    tenant = get_tenant_by_id(tenant_id) if tenant_id else None
    if not tenant:
        return 'unknown'
    kind = tenant.get('tenant_type') or ('DEMO' if tenant.get('is_demo') else 'standard')
    return str(kind)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Correct SE-4060 inflated invoice totals (dry run by default).')
    parser.add_argument('--ids', required=True, help='Comma-separated invoice ids (from the SE-4062 plan).')
    parser.add_argument('--apply', action='store_true', help='Write the corrections. Without it nothing is written.')
    args = parser.parse_args(argv)

    ids = [part.strip() for part in args.ids.split(',') if part.strip()]
    if not ids:
        parser.error('--ids needs at least one invoice id')

    from smart_invoice_pro.utils.cosmos_client import invoices_container

    cache = {}

    def tenant_kind(tenant_id):
        if tenant_id not in cache:
            cache[tenant_id] = _tenant_kind(tenant_id)
        return cache[tenant_id]

    counts = correct(
        ids, _make_fetch(invoices_container), _make_save(invoices_container), _log_change, tenant_kind,
        apply=args.apply,
    )
    mode = 'APPLY' if args.apply else 'DRY RUN (nothing written)'
    print(f'{mode}: ' + ', '.join(f'{k} {v}' for k, v in counts.items()), file=sys.stderr)
    return 1 if counts['refused'] or counts['not_found'] else 0


if __name__ == '__main__':
    sys.exit(main())

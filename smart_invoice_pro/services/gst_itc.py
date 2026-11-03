"""
Input-tax (ITC) summary from purchase bills.

Turns a tenant's bills for one tax period into the purchase side a CA needs
for GSTR-3B Table 4 and for matching against GSTR-2B:

    b2b          bills from registered vendors (vendor has a valid GSTIN),
                 one entry per vendor, with tax split by rate
    itc          eligible input tax from those bills, by rate and in total
    unregistered bills from vendors without a GSTIN: no input tax credit

Pure functions only: callers load the bills, vendors and the buyer's GSTIN.
A bill it cannot place is reported in `issues` instead of being guessed.
Reverse charge, imports and blocked credits (Section 17(5)) are not handled.
"""
from collections import defaultdict

from smart_invoice_pro.api.gst_api import validate_gstin_format
from smart_invoice_pro.services.gstr1 import _line_amounts, _num, _portal_date, _r2, period_code

# Bills that are not (or no longer) a purchase.
EXCLUDED_PAYMENT_STATUSES = {'Draft'}


def _tax_row():
    return {'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0}


def _rounded(row):
    return {k: _r2(v) for k, v in row.items()}


def build_itc_summary(bills, vendors_by_id, buyer_gstin, year, month):
    """Return (summary_json, issues, bill_count).

    bills          bill documents (any status; filtered here)
    vendors_by_id  {vendor id (str): vendor document}
    buyer_gstin    the organisation's GSTIN
    """
    issues = []
    buyer_gstin = str(buyer_gstin or '').strip().upper()
    if not validate_gstin_format(buyer_gstin):
        issues.append({'bill': None, 'problem': 'Organisation GSTIN is missing or invalid.'})
    buyer_state = buyer_gstin[:2] if buyer_gstin else ''

    prefix = f'{year:04d}-{month:02d}'
    b2b = defaultdict(list)
    itc_by_rate = defaultdict(_tax_row)
    unregistered = _tax_row()
    counted = 0

    for bill in bills:
        bill_date = str(bill.get('bill_date') or '')[:10]
        if not bill_date.startswith(prefix):
            continue
        if str(bill.get('payment_status') or '') in EXCLUDED_PAYMENT_STATUSES or \
                str(bill.get('lifecycle_status') or bill.get('status') or '').upper() == 'ARCHIVED':
            continue
        number = str(bill.get('bill_number') or bill.get('id') or '')

        def flag(problem):
            issues.append({'bill': number, 'problem': problem})

        lines = [it for it in (bill.get('items') or []) if isinstance(it, dict)]
        if not lines:
            flag('Bill has no item lines; its tax cannot be split by rate.')
            continue

        vendor = vendors_by_id.get(str(bill.get('vendor_id'))) or {}
        if not vendor:
            flag('Vendor not found.')
        ctin = str(vendor.get('gst_number') or vendor.get('gstin') or '').strip().upper()
        invalid_gstin = bool(ctin) and not validate_gstin_format(ctin)
        if invalid_gstin:
            flag(f'Vendor GSTIN {ctin} is not a valid GSTIN; no input tax credit taken.')
            ctin = ''

        inter_state = bool(ctin) and bool(buyer_state) and ctin[:2] != buyer_state
        rate_rows = defaultdict(_tax_row)
        for item in lines:
            _, txval, rt, iamt, camt, samt = _line_amounts(item, inter_state)
            row = rate_rows[rt]
            row['txval'] += txval
            row['iamt'] += iamt
            row['camt'] += camt
            row['samt'] += samt
        counted += 1

        if not ctin:
            if vendor and not invalid_gstin:
                flag('Vendor has no GSTIN: no input tax credit.')
            for r in rate_rows.values():
                for k in unregistered:
                    unregistered[k] += r[k]
            continue

        stored_igst, stored_cgst = _num(bill.get('igst_amount')), _num(bill.get('cgst_amount'))
        if (inter_state and stored_cgst > 0) or (not inter_state and stored_igst > 0):
            flag(f"Bill was saved with {'CGST/SGST' if inter_state else 'IGST'} but the vendor is "
                 f"{'out of' if inter_state else 'in'} state; this summary uses "
                 f"{'IGST' if inter_state else 'CGST/SGST'}.")
        for rt, r in rate_rows.items():
            for k in r:
                itc_by_rate[rt][k] += r[k]
        b2b[ctin].append({
            'inum': number,
            'idt': _portal_date(bill_date),
            'val': _r2(_num(bill.get('total_amount'))),
            'pos': buyer_state,
            'itms': [{'num': i, 'itm_det': {'rt': rt, **_rounded(r), 'csamt': 0}}
                     for i, (rt, r) in enumerate(sorted(rate_rows.items()), start=1)],
        })

    itc_total = _tax_row()
    for r in itc_by_rate.values():
        for k in itc_total:
            itc_total[k] += r[k]

    summary = {
        'gstin': buyer_gstin,
        'fp': period_code(year, month),
        'b2b': [{'ctin': ctin, 'inv': docs} for ctin, docs in sorted(b2b.items())],
        'itc': {
            'by_rate': [{'rt': rt, **_rounded(r)} for rt, r in sorted(itc_by_rate.items())],
            'total': _rounded(itc_total),
        },
        'unregistered': _rounded(unregistered),
    }
    return summary, issues, counted

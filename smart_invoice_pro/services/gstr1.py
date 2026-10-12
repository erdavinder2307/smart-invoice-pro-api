"""
GSTR-1 return builder.

Turns a tenant's sales invoices for one tax period into the JSON layout the
GST portal's GSTR-1 offline tool imports:

    b2b   registered buyers (buyer has a GSTIN), one entry per buyer
    b2cl  unregistered buyers, inter-state, invoice value above B2CL_LIMIT
    b2cs  every other unregistered sale, summed by place of supply and rate
    hsn   Table 12, split into hsn_b2b and hsn_b2c

Pure functions only: callers load the invoices, customers, products and the
seller's GSTIN, so this module is testable without Cosmos.

Books has no credit-note records yet, so cdnr/cdnur are never produced; an
invoice it cannot place in a table is reported in `issues` instead of being
guessed.
"""
import re
from collections import defaultdict
from datetime import datetime

from smart_invoice_pro.api.gst_api import validate_gstin_format

# Inter-state B2C invoices above this value go to B2CL (Notification 12/2024-CT, from Aug 2024).
B2CL_LIMIT = 100000.0

# Statuses that are not (or no longer) a supply.
EXCLUDED_STATUSES = {'Draft', 'Cancelled', 'ARCHIVED'}

ZERO_RATED_TREATMENTS = {'special_economic_zone', 'deemed_export', 'export'}

STATE_CODES = {
    'jammu & kashmir': '01', 'jammu and kashmir': '01', 'himachal pradesh': '02', 'punjab': '03',
    'chandigarh': '04', 'uttarakhand': '05', 'haryana': '06', 'delhi': '07', 'rajasthan': '08',
    'uttar pradesh': '09', 'bihar': '10', 'sikkim': '11', 'arunachal pradesh': '12',
    'nagaland': '13', 'manipur': '14', 'mizoram': '15', 'tripura': '16', 'meghalaya': '17',
    'assam': '18', 'west bengal': '19', 'jharkhand': '20', 'odisha': '21', 'chhattisgarh': '22',
    'madhya pradesh': '23', 'gujarat': '24', 'daman & diu': '25', 'daman and diu': '25',
    'dadra & nagar haveli': '26', 'dadra and nagar haveli': '26',
    'dadra and nagar haveli and daman and diu': '26', 'maharashtra': '27',
    'karnataka': '29', 'goa': '30', 'lakshadweep': '31', 'kerala': '32', 'tamil nadu': '33',
    'puducherry': '34', 'andaman & nicobar islands': '35', 'andaman and nicobar islands': '35',
    'telangana': '36', 'andhra pradesh': '37', 'ladakh': '38', 'other territory': '97',
}

_HSN_RE = re.compile(r'^[0-9]{4,8}$')


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(value):
    return round(value + 0.0, 2)


def state_code(value):
    """'Punjab', '03' or '3' -> '03'; '' when unknown."""
    text = str(value or '').strip()
    if text.isdigit() and 1 <= int(text) <= 99:
        return f'{int(text):02d}'
    return STATE_CODES.get(text.lower(), '')


def period_code(year, month):
    """GSTR-1 'fp' field: MMYYYY."""
    return f'{month:02d}{year:04d}'


def _portal_date(iso_date):
    return datetime.strptime(iso_date, '%Y-%m-%d').strftime('%d-%m-%Y')


def _line_hsn(item, products_by_name):
    code = str(item.get('hsn_sac') or item.get('hsn_code') or item.get('hsn') or '').strip()
    product = products_by_name.get(str(item.get('name') or '').strip().lower())
    if not code and product:
        code = str(product.get('hsn_sac') or '').strip()
    unit = str(item.get('unit') or (product or {}).get('unit') or '').strip().upper()
    return code, unit


def _line_amounts(item, inter_state):
    qty = max(0.0, _num(item.get('quantity')))
    rate = max(0.0, _num(item.get('rate')))
    txval = max(0.0, qty * rate - max(0.0, _num(item.get('discount'))))
    rt = max(0.0, _num(item.get('tax')))
    if 'igst' in item or 'cgst' in item:
        iamt, camt, samt = _num(item.get('igst')), _num(item.get('cgst')), _num(item.get('sgst'))
    elif inter_state:
        iamt, camt, samt = _r2(txval * rt / 100), 0.0, 0.0
    else:
        half = _r2(txval * rt / 200)
        iamt, camt, samt = 0.0, half, half
    return qty, txval, rt, iamt, camt, samt


def build_gstr1(invoices, customers_by_id, products_by_name, seller_gstin, year, month):
    """Return (gstr1_json, issues, invoice_count).

    invoices          invoice documents (any status; filtered here)
    customers_by_id   {customer id (str): customer document}
    products_by_name  {lower-cased product name: product document}, for HSN and unit
    seller_gstin      the organisation's GSTIN
    """
    issues = []
    seller_gstin = str(seller_gstin or '').strip().upper()
    if not validate_gstin_format(seller_gstin):
        issues.append({'invoice': None, 'problem': 'Organisation GSTIN is missing or invalid.'})
    seller_state = seller_gstin[:2] if seller_gstin else ''

    prefix = f'{year:04d}-{month:02d}'
    b2b = defaultdict(list)
    b2cl = defaultdict(list)
    b2cs = defaultdict(lambda: {'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})
    hsn = {'b2b': defaultdict(lambda: {'qty': 0.0, 'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0}),
           'b2c': defaultdict(lambda: {'qty': 0.0, 'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})}
    counted = 0

    for inv in invoices:
        issue_date = str(inv.get('issue_date') or '')
        if not issue_date.startswith(prefix):
            continue
        if str(inv.get('status') or '') in EXCLUDED_STATUSES or \
                str(inv.get('lifecycle_status') or '').upper() == 'ARCHIVED':
            continue
        if not inv.get('is_gst_applicable'):
            continue
        number = str(inv.get('invoice_number') or inv.get('id') or '')

        def flag(problem):
            issues.append({'invoice': number, 'problem': problem})

        if inv.get('gst_treatment') in ZERO_RATED_TREATMENTS:
            flag('Export/SEZ supply: not included (EXP/SEZ tables are not built yet).')
            continue

        customer = customers_by_id.get(str(inv.get('customer_id'))) or {}
        ctin = str(customer.get('gst_number') or customer.get('gstin') or '').strip().upper()
        registered = bool(ctin) and inv.get('gst_treatment') not in ('unregistered', 'consumer')
        if ctin and not validate_gstin_format(ctin):
            flag(f'Customer GSTIN {ctin} is not a valid GSTIN; treated as unregistered.')
            registered = False

        pos = state_code(inv.get('place_of_supply')) or \
            (ctin[:2] if registered else '') or \
            state_code(customer.get('place_of_supply') or customer.get('billing_state'))
        if not pos:
            flag('Place of supply is missing.')
            continue
        inter_state = pos != seller_state

        lines = [it for it in (inv.get('items') or []) if isinstance(it, dict)]
        rate_rows = defaultdict(lambda: {'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})
        for item in lines:
            qty, txval, rt, iamt, camt, samt = _line_amounts(item, inter_state)
            row = rate_rows[rt]
            row['txval'] += txval
            row['iamt'] += iamt
            row['camt'] += camt
            row['samt'] += samt

            code, unit = _line_hsn(item, products_by_name)
            if not _HSN_RE.match(code):
                flag(f"Line '{item.get('name', '')}': HSN/SAC code is missing or invalid.")
                code = code or 'MISSING'
            is_service = code.startswith('99')
            key = (code, 'NA' if is_service else (unit or 'OTH'), rt)
            h = hsn['b2b' if registered else 'b2c'][key]
            h['qty'] += 0.0 if is_service else qty
            h['txval'] += txval
            h['iamt'] += iamt
            h['camt'] += camt
            h['samt'] += samt

        if not rate_rows:
            flag('Invoice has no lines.')
            continue
        counted += 1
        value = _r2(_num(inv.get('total_amount')))
        itms = [
            {'num': i, 'itm_det': {'rt': rt, 'txval': _r2(r['txval']), 'iamt': _r2(r['iamt']),
                                    'camt': _r2(r['camt']), 'samt': _r2(r['samt']), 'csamt': 0}}
            for i, (rt, r) in enumerate(sorted(rate_rows.items()), start=1)
        ]
        doc = {'inum': number, 'idt': _portal_date(issue_date), 'val': value, 'itms': itms}

        if registered:
            b2b[ctin].append({**doc, 'pos': pos, 'rchrg': 'N', 'inv_typ': 'R'})
        elif inter_state and value > B2CL_LIMIT:
            b2cl[pos].append(doc)
        else:
            for rt, r in rate_rows.items():
                row = b2cs[('INTER' if inter_state else 'INTRA', pos, rt)]
                for k in row:
                    row[k] += r[k]

    def hsn_rows(table):
        out = []
        for i, ((code, uqc, rt), h) in enumerate(sorted(table.items()), start=1):
            out.append({'num': i, 'hsn_sc': code, 'uqc': uqc, 'qty': _r2(h['qty']), 'rt': rt,
                        'txval': _r2(h['txval']), 'iamt': _r2(h['iamt']), 'camt': _r2(h['camt']),
                        'samt': _r2(h['samt']), 'csamt': 0})
        return out

    payload = {
        'gstin': seller_gstin,
        'fp': period_code(year, month),
        'version': 'GST3.2',
        'hash': 'hash',
        'b2b': [{'ctin': ctin, 'inv': docs} for ctin, docs in sorted(b2b.items())],
        'b2cl': [{'pos': pos, 'inv': docs} for pos, docs in sorted(b2cl.items())],
        'b2cs': [
            {'sply_ty': sply, 'pos': pos, 'typ': 'OE', 'rt': rt, 'txval': _r2(r['txval']),
             'iamt': _r2(r['iamt']), 'camt': _r2(r['camt']), 'samt': _r2(r['samt']), 'csamt': 0}
            for (sply, pos, rt), r in sorted(b2cs.items())
        ],
        'hsn': {'hsn_b2b': hsn_rows(hsn['b2b']), 'hsn_b2c': hsn_rows(hsn['b2c'])},
    }
    for table in ('b2b', 'b2cl', 'b2cs'):
        if not payload[table]:
            del payload[table]
    return payload, issues, counted

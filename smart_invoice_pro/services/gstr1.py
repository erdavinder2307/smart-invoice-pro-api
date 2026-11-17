"""
GSTR-1 return builder.

Turns a tenant's sales invoices and credit/debit notes for one tax period
into the JSON layout the GST portal's GSTR-1 offline tool imports:

    b2b    registered buyers (buyer has a GSTIN), one entry per buyer
    b2cl   unregistered buyers, inter-state, invoice value above B2CL_LIMIT
    b2cs   every other unregistered sale, summed by place of supply and rate,
           **net of credit/debit notes against B2CS invoices**
    cdnr   Table 9B: notes against invoices to registered buyers, per buyer
    cdnur  Table 9B: notes against B2CL invoices (unregistered, inter-state,
           above the limit), type B2CL
    nil    Table 8, lines taxed at 0% (Books does not record nil-rated vs
           exempt, so they are reported as nil-rated with an issue to check)
    hsn    Table 12, split into hsn_b2b and hsn_b2c, net of notes

Pure functions only: callers load the invoices, notes, customers, products
and the seller's GSTIN, so this module is testable without Cosmos.

A note is placed by the table its *original* invoice belongs to, which is
how the portal wants it: a note against a B2B invoice goes to CDNR whatever
its own value, a note against a B2CL invoice to CDNUR, and a note against a
B2CS invoice is netted into B2CS (there is no per-note row for those).
The original invoice may be from an earlier month, so callers pass every
original in ``invoices_by_id``. Since the delinking of notes from invoices
(Sept 2020) the portal layout carries no original invoice number on a note;
the reference stays on the note document and in the API.

Anything that cannot be placed is reported in `issues` instead of being
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

# GST portal unit codes (UQC) and the free-text units Books users type for them.
UQC_CODES = {
    'BAG', 'BAL', 'BDL', 'BKL', 'BOU', 'BOX', 'BTL', 'BUN', 'CAN', 'CBM', 'CCM', 'CMS', 'CTN', 'DOZ',
    'DRM', 'GGK', 'GMS', 'GRS', 'GYD', 'KGS', 'KLR', 'KME', 'LTR', 'MLT', 'MTR', 'MTS', 'NOS', 'OTH',
    'PAC', 'PCS', 'PRS', 'QTL', 'ROL', 'SET', 'SQF', 'SQM', 'SQY', 'TBS', 'TGM', 'THD', 'TON', 'TUB',
    'UGS', 'UNT', 'YDS',
}
UNIT_ALIASES = {
    'PC': 'PCS', 'PIECE': 'PCS', 'PIECES': 'PCS', 'NO': 'NOS', 'NUMBER': 'NOS', 'NUMBERS': 'NOS',
    'EACH': 'NOS', 'EA': 'NOS', 'KG': 'KGS', 'KILOGRAM': 'KGS', 'KILOGRAMS': 'KGS', 'G': 'GMS',
    'GM': 'GMS', 'GRAM': 'GMS', 'GRAMS': 'GMS', 'L': 'LTR', 'LITRE': 'LTR', 'LITRES': 'LTR',
    'LITER': 'LTR', 'LITERS': 'LTR', 'ML': 'MLT', 'M': 'MTR', 'METER': 'MTR', 'METERS': 'MTR',
    'METRE': 'MTR', 'METRES': 'MTR', 'CM': 'CMS', 'BOXES': 'BOX', 'DOZEN': 'DOZ', 'PACK': 'PAC',
    'PACKET': 'PAC', 'PACKETS': 'PAC', 'SETS': 'SET', 'PAIR': 'PRS', 'PAIRS': 'PRS', 'BOTTLE': 'BTL',
    'BOTTLES': 'BTL', 'BAGS': 'BAG', 'UNIT': 'UNT', 'UNITS': 'UNT', 'TONNE': 'TON', 'TONNES': 'TON',
    'SQFT': 'SQF', 'SQ FT': 'SQF', 'SQMT': 'SQM', 'SQ M': 'SQM', 'ROLL': 'ROL', 'ROLLS': 'ROL',
    'CARTON': 'CTN', 'CARTONS': 'CTN', 'QUINTAL': 'QTL',
}

# GSTR-1 invoice and note numbers: at most 16 characters, letters, digits, '-' and '/'
# (Rule 46(b), Rule 53(1A)(b)).
_INUM_RE = re.compile(r'^[A-Za-z0-9/-]{1,16}$')


def uqc_code(unit):
    """Free-text unit -> (UQC code, known). Empty or unknown units become 'OTH'."""
    text = str(unit or '').strip().upper().rstrip('.')
    if text in UQC_CODES:
        return text, True
    if text in UNIT_ALIASES:
        return UNIT_ALIASES[text], True
    return 'OTH', not text


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
    if item.get('taxable_value') is not None:
        txval = max(0.0, _num(item.get('taxable_value')))
    else:
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


def _is_skipped(doc, date_field, prefix):
    """True for documents outside the period or that are not (or no longer) a supply."""
    if not str(doc.get(date_field) or '').startswith(prefix):
        return True
    if str(doc.get('status') or '') in EXCLUDED_STATUSES or \
            str(doc.get('lifecycle_status') or '').upper() == 'ARCHIVED':
        return True
    return not doc.get('is_gst_applicable')


def _classify(inv, customers_by_id, seller_state):
    """Where an invoice belongs, from its buyer and place of supply.

    Returns a dict with registered, ctin, pos, inter_state and value, plus
    'warnings' (reported but not blocking) and 'problem' (blocking) texts.
    """
    out = {'registered': False, 'ctin': '', 'pos': '', 'inter_state': False,
           'value': _r2(_num(inv.get('total_amount'))), 'warnings': [], 'problem': None}
    customer = customers_by_id.get(str(inv.get('customer_id'))) or {}
    ctin = str(customer.get('gst_number') or customer.get('gstin') or '').strip().upper()
    registered = bool(ctin) and inv.get('gst_treatment') not in ('unregistered', 'consumer')
    if ctin and not validate_gstin_format(ctin):
        out['warnings'].append(f'Customer GSTIN {ctin} is not a valid GSTIN; treated as unregistered.')
        registered = False
    pos = state_code(inv.get('place_of_supply')) or \
        (ctin[:2] if registered else '') or \
        state_code(customer.get('place_of_supply') or customer.get('billing_state'))
    if not pos:
        out['problem'] = 'Place of supply is missing.'
        return out
    out.update({'registered': registered, 'ctin': ctin if registered else '', 'pos': pos,
                'inter_state': pos != seller_state})
    return out


def _split_contradicts(amounts, inter_state):
    """Stored IGST on an intra-state supply (or CGST/SGST on an inter-state one) means the
    document was taxed for a different place of supply than the one derived here."""
    return any((not inter_state and iamt > 0) or (inter_state and (camt > 0 or samt > 0))
               for _, _, _, iamt, camt, samt in amounts)


def build_gstr1(invoices, customers_by_id, products_by_name, seller_gstin, year, month,
                credit_notes=(), invoices_by_id=None, stats=None):
    """Return (gstr1_json, issues, invoice_count).

    invoices          invoice documents (any status; filtered here)
    customers_by_id   {customer id (str): customer document}
    products_by_name  {lower-cased product name: product document}, for HSN and unit
    seller_gstin      the organisation's GSTIN
    credit_notes      credit/debit note documents (any status; filtered here)
    invoices_by_id    {invoice id (str): invoice document} for the notes' originals, which
                      may be from earlier months; defaults to the invoices given
    stats             optional dict; receives {'notes': number of notes included}
    """
    issues = []
    seller_gstin = str(seller_gstin or '').strip().upper()
    if not validate_gstin_format(seller_gstin):
        issues.append({'invoice': None, 'problem': 'Organisation GSTIN is missing or invalid.'})
    seller_state = seller_gstin[:2] if seller_gstin else ''
    if invoices_by_id is None:
        invoices_by_id = {str(inv.get('id')): inv for inv in invoices if inv.get('id')}

    prefix = f'{year:04d}-{month:02d}'
    b2b = defaultdict(list)
    b2cl = defaultdict(list)
    b2cs = defaultdict(lambda: {'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})
    cdnr = defaultdict(list)
    cdnur = []
    nil = defaultdict(float)
    hsn = {'b2b': defaultdict(lambda: {'qty': 0.0, 'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0}),
           'b2c': defaultdict(lambda: {'qty': 0.0, 'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})}
    counted = 0
    counted_notes = 0

    def tally_lines(lines, amounts, registered, inter_state, sign, flag):
        """Rate rows for a document's lines; nil and HSN tables updated in place."""
        rate_rows = defaultdict(lambda: {'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0})
        for item, (qty, txval, rt, iamt, camt, samt) in zip(lines, amounts):
            if rt == 0:
                nil[('INTR' if inter_state else 'INTRA') + ('B2B' if registered else 'B2C')] += sign * txval
            else:
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
            uqc, known = ('NA', True) if is_service else uqc_code(unit)
            if not known:
                flag(f"Line '{item.get('name', '')}': unit '{unit}' has no GST unit code; reported as OTH.")
            h = hsn['b2b' if registered else 'b2c'][(code, uqc, rt)]
            h['qty'] += 0.0 if is_service else sign * qty
            h['txval'] += sign * txval
            h['iamt'] += sign * iamt
            h['camt'] += sign * camt
            h['samt'] += sign * samt
        if any(rt == 0 for _, _, rt, _, _, _ in amounts):
            flag('Lines taxed at 0% are reported as nil-rated in Table 8; '
                 'Books does not record exempt or non-GST supplies, so check them.')
        return rate_rows

    def rate_items(rate_rows, inter_only=False):
        out = []
        for i, (rt, r) in enumerate(sorted(rate_rows.items()), start=1):
            det = {'rt': rt, 'txval': _r2(r['txval']), 'iamt': _r2(r['iamt'])}
            if not inter_only:
                det.update({'camt': _r2(r['camt']), 'samt': _r2(r['samt'])})
            det['csamt'] = 0
            out.append({'num': i, 'itm_det': det})
        return out

    def add_b2cs(rate_rows, inter_state, pos, sign):
        for rt, r in rate_rows.items():
            row = b2cs[('INTER' if inter_state else 'INTRA', pos, rt)]
            for k in row:
                row[k] += sign * r[k]

    # ── Invoices ───────────────────────────────────────────────────────────
    for inv in invoices:
        if _is_skipped(inv, 'issue_date', prefix):
            continue
        number = str(inv.get('invoice_number') or inv.get('id') or '')

        def flag(problem, number=number):
            issues.append({'invoice': number, 'problem': problem})

        if not _INUM_RE.match(number):
            flag('Invoice number must be 1-16 characters (letters, digits, "-" or "/") for the GST portal.')

        if inv.get('gst_treatment') in ZERO_RATED_TREATMENTS:
            flag('Export/SEZ supply: not included (EXP/SEZ tables are not built yet).')
            continue

        cls = _classify(inv, customers_by_id, seller_state)
        for warning in cls['warnings']:
            flag(warning)
        if cls['problem']:
            flag(cls['problem'])
            continue
        registered, ctin, pos, inter_state = cls['registered'], cls['ctin'], cls['pos'], cls['inter_state']

        lines = [it for it in (inv.get('items') or []) if isinstance(it, dict)]
        if not lines:
            flag('Invoice has no lines.')
            continue
        amounts = [_line_amounts(item, inter_state) for item in lines]
        if _split_contradicts(amounts, inter_state):
            flag(f"Invoice is {'inter' if inter_state else 'intra'}-state for place of supply {pos} "
                 f"but was taxed as {'CGST/SGST' if inter_state else 'IGST'}; not included.")
            continue

        rate_rows = tally_lines(lines, amounts, registered, inter_state, 1, flag)
        counted += 1
        if not rate_rows:
            continue
        value = cls['value']
        doc = {'inum': number, 'idt': _portal_date(str(inv.get('issue_date'))), 'val': value,
               'itms': rate_items(rate_rows)}

        if registered:
            b2b[ctin].append({**doc, 'pos': pos, 'rchrg': 'N', 'inv_typ': 'R'})
        elif inter_state and value > B2CL_LIMIT:
            b2cl[pos].append(doc)
        else:
            add_b2cs(rate_rows, inter_state, pos, 1)

    # ── Credit and debit notes (Table 9B) ──────────────────────────────────
    for note in credit_notes:
        if _is_skipped(note, 'note_date', prefix):
            continue
        number = str(note.get('note_number') or note.get('id') or '')
        is_debit = note.get('note_type') == 'debit'
        ntty, sign = ('D', 1) if is_debit else ('C', -1)
        label = f"{'Debit' if is_debit else 'Credit'} note {number}"

        def flag(problem, number=number):
            issues.append({'invoice': number, 'problem': problem})

        if not _INUM_RE.match(number):
            flag('Note number must be 1-16 characters (letters, digits, "-" or "/") for the GST portal.')

        original = invoices_by_id.get(str(note.get('invoice_id')))
        if original is None:
            flag(f'{label}: original invoice {note.get("invoice_number") or ""} not found; not included.')
            continue
        if original.get('gst_treatment') in ZERO_RATED_TREATMENTS:
            flag(f'{label}: against an export/SEZ invoice; not included (EXP/SEZ tables are not built yet).')
            continue
        cls = _classify(original, customers_by_id, seller_state)
        for warning in cls['warnings']:
            flag(f'{label}: {warning}')
        if cls['problem']:
            flag(f"{label}: original invoice {original.get('invoice_number') or ''}: {cls['problem']} Not included.")
            continue
        registered, ctin, pos, inter_state = cls['registered'], cls['ctin'], cls['pos'], cls['inter_state']

        if not is_debit:
            late = _section34_late(note, original)
            if late:
                flag(f'{label}: {late} Not included; check with your CA.')
                continue

        lines = [it for it in (note.get('items') or []) if isinstance(it, dict)]
        if not lines:
            flag(f'{label} has no lines.')
            continue
        amounts = [_line_amounts(item, inter_state) for item in lines]
        if _split_contradicts(amounts, inter_state):
            flag(f"{label} is {'inter' if inter_state else 'intra'}-state for place of supply {pos} "
                 f"but was taxed as {'CGST/SGST' if inter_state else 'IGST'}; not included.")
            continue

        rate_rows = tally_lines(lines, amounts, registered, inter_state, sign, flag)
        counted_notes += 1
        if not rate_rows:
            continue
        doc = {'ntty': ntty, 'nt_num': number, 'nt_dt': _portal_date(str(note.get('note_date'))),
               'p_gst': 'N', 'val': _r2(_num(note.get('total_amount')))}

        if registered:
            cdnr[ctin].append({**doc, 'pos': pos, 'rchrg': 'N', 'inv_typ': 'R',
                               'itms': rate_items(rate_rows)})
        elif inter_state and cls['value'] > B2CL_LIMIT:
            cdnur.append({'typ': 'B2CL', **doc, 'pos': pos, 'itms': rate_items(rate_rows, inter_only=True)})
        else:
            add_b2cs(rate_rows, inter_state, pos, sign)

    # ── Netted tables that went negative ───────────────────────────────────
    for (sply, pos, rt), r in sorted(b2cs.items()):
        if r['txval'] < -0.005:
            issues.append({'invoice': None, 'problem': (
                f'B2CS {sply.lower()}-state, place of supply {pos}, {rt:g}%: credit notes exceed the '
                f"month's sales ({_r2(r['txval'])}); the portal does not accept a negative B2CS row.")})
    for sply, amt in sorted(nil.items()):
        if amt < -0.005:
            issues.append({'invoice': None, 'problem': (
                f"Nil-rated {sply}: credit notes exceed the month's nil-rated sales ({_r2(amt)}).")})

    def hsn_rows(table):
        out = []
        for i, ((code, uqc, rt), h) in enumerate(sorted(table.items()), start=1):
            if h['txval'] < -0.005:
                issues.append({'invoice': None, 'problem': (
                    f"HSN {code} at {rt:g}%: net of credit notes this month's row is negative "
                    f"({_r2(h['txval'])}); check it before filing.")})
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
        'cdnr': [{'ctin': ctin, 'nt': docs} for ctin, docs in sorted(cdnr.items())],
        'cdnur': cdnur,
        'nil': {'inv': [{'sply_ty': sply, 'expt_amt': 0, 'nil_amt': _r2(amt), 'ngsup_amt': 0}
                        for sply, amt in sorted(nil.items())]},
        'hsn': {'hsn_b2b': hsn_rows(hsn['b2b']), 'hsn_b2c': hsn_rows(hsn['b2c'])},
    }
    for table in ('b2b', 'b2cl', 'b2cs', 'cdnr', 'cdnur'):
        if not payload[table]:
            del payload[table]
    if not nil:
        del payload['nil']
    if stats is not None:
        stats['notes'] = counted_notes
    return payload, issues, counted


def _section34_late(note, original):
    """The Section 34(2) warning text for a credit note dated after its deadline, else None."""
    # Imported here: services.credit_notes imports this module for state codes and HSN.
    from smart_invoice_pro.services.credit_notes import parse_iso_date, section34_issue
    late = section34_issue(parse_iso_date(note.get('note_date')),
                           parse_iso_date(original.get('issue_date')))
    return late['problem'] if late else None

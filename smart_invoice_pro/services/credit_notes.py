"""
Credit and debit notes against a tax invoice (Section 34, CGST Act; Rule 53).

Pure functions only: the API loads the invoice, the customer, the products and
the notes already issued, and persists what comes back. Nothing here touches
Cosmos, so every rule is testable on example data.

What a note holds (Rule 53(1A)):
    - its own serial number and date (``note_number``, ``note_date``);
    - the original invoice's number and date (``invoice_number``,
      ``invoice_date``) and id;
    - the recipient (customer snapshot, GSTIN when registered);
    - per line: the taxable value, the rate and the tax credited or debited;
    - the reason (``reason``, one of REASONS, plus a free-text ``reason_note``).

Tax on a note follows the original invoice: the same place of supply, the
same CGST/SGST-or-IGST split, the same rate per line, and the same per-line
paise rounding as ``calculate_gst``. A credit note can never credit more
than was invoiced — per line, per tax head and in total — counting every
earlier credit note on that invoice.

Section 34(2): a credit note is declared in the return for the month it is
issued, but no later than 30 November following the end of the financial
year of the supply (or the annual return date, if earlier). That is a
warning here, not a block, in line with the Rule 46 checks.
"""
import re
from datetime import date, datetime

from smart_invoice_pro.api.gst_api import validate_gstin_format
from smart_invoice_pro.services.gstr1 import _HSN_RE, _line_hsn, state_code
from smart_invoice_pro.services.rule46 import REGISTERED_TREATMENTS, _has_address

NOTE_TYPES = ('credit', 'debit')

# Section 34(1) (credit) and 34(3) (debit) grounds, plus the usual commercial ones.
REASONS = {
    'sales_return': 'Goods returned by the recipient',
    'post_sale_discount': 'Discount given after the supply',
    'deficiency': 'Goods or services found deficient',
    'value_correction': 'Taxable value on the invoice was wrong',
    'tax_correction': 'Tax charged on the invoice was wrong',
    'other': 'Other (see note)',
}

# Rule 53(1A)(b): a consecutive serial number of at most 16 characters.
NOTE_NUMBER_RE = re.compile(r'^[A-Za-z0-9/-]{1,16}$')

# Statuses in which the invoice still has something to settle.
OPEN_INVOICE_STATUSES = {'Issued', 'Partially Paid', 'Overdue'}

# Tolerance for comparing rupee amounts that were rounded to paise.
_EPS = 0.005


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(value):
    return round(value + 0.0, 2)


def parse_iso_date(value):
    try:
        return datetime.strptime(str(value or ''), '%Y-%m-%d').date()
    except ValueError:
        return None


# ── Section 34(2): time limit for declaring a credit note ─────────────────────

def financial_year_end(day):
    """31 March that closes the financial year [day] falls in (April–March)."""
    return date(day.year if day.month <= 3 else day.year + 1, 3, 31)


def section34_deadline(invoice_date, annual_return_date=None):
    """The last day a credit note for a supply dated [invoice_date] may be declared.

    30 November following the end of that financial year, or the date the
    annual return was filed if that came first (Section 34(2), as amended
    from 1 October 2022).
    """
    deadline = date(financial_year_end(invoice_date).year, 11, 30)
    if annual_return_date and annual_return_date < deadline:
        return annual_return_date
    return deadline


def section34_issue(note_date, invoice_date, annual_return_date=None):
    """A warning when a credit note is dated after its Section 34(2) deadline, else None."""
    if note_date is None or invoice_date is None:
        return None
    deadline = section34_deadline(invoice_date, annual_return_date)
    if note_date <= deadline:
        return None
    return {
        'field': 'note_date',
        'problem': (f'Section 34(2): a credit note for the invoice dated '
                    f'{invoice_date.strftime("%d-%m-%Y")} had to be declared by '
                    f'{deadline.strftime("%d-%m-%Y")}; dated after that, the tax on it '
                    f'cannot reduce your output liability in GSTR-1.'),
    }


# ── Tax on a note ─────────────────────────────────────────────────────────────

def line_taxable(item):
    """Taxable value of one line: quantity × rate − line discount, never negative."""
    qty = max(0.0, _num(item.get('quantity')))
    rate = max(0.0, _num(item.get('rate')))
    return max(0.0, qty * rate - max(0.0, _num(item.get('discount'))))


def invoice_tax_type(invoice, seller_state=''):
    """'CGST_SGST', 'IGST' or 'NONE', as the original invoice was taxed.

    Read from the stored amounts first (that is what was actually charged);
    an invoice taxed at 0% throughout is placed by its place of supply
    against [seller_state], so a note on it still carries the right split.
    """
    if not invoice.get('is_gst_applicable'):
        return 'NONE'
    lines = [it for it in (invoice.get('items') or []) if isinstance(it, dict)]
    if _num(invoice.get('igst_amount')) > 0 or any(_num(it.get('igst')) > 0 for it in lines):
        return 'IGST'
    if _num(invoice.get('cgst_amount')) > 0 or _num(invoice.get('sgst_amount')) > 0 or \
            any(_num(it.get('cgst')) > 0 or _num(it.get('sgst')) > 0 for it in lines):
        return 'CGST_SGST'
    pos = state_code(invoice.get('place_of_supply'))
    seller = state_code(seller_state)
    if pos and seller:
        return 'CGST_SGST' if pos == seller else 'IGST'
    return 'CGST_SGST'


def tax_split(taxable, rate, tax_type):
    """(cgst, sgst, igst) for [taxable] at [rate] %, rounded to paise per line exactly
    as calculate_gst does: intra-state halves are rounded independently."""
    if tax_type == 'IGST':
        return 0.0, 0.0, _r2(taxable * rate / 100)
    if tax_type == 'CGST_SGST':
        half = _r2(taxable * (rate / 2) / 100)
        return half, half, 0.0
    return 0.0, 0.0, 0.0


def build_note_lines(invoice, requested, products_by_name, tax_type):
    """Turn the lines a user asked to credit into note lines priced off the invoice.

    requested  [{'invoice_line_index': int, 'quantity': n, 'rate': r?, 'discount': d?}]
               quantity defaults to the invoiced quantity, rate to the invoiced rate;
               both may be lower (a partial return, a post-sale discount per unit),
               never higher.
    Returns (lines, errors) where errors is {field: message}.
    """
    errors = {}
    lines = []
    invoice_lines = [it for it in (invoice.get('items') or []) if isinstance(it, dict)]
    if not isinstance(requested, list) or not requested:
        return [], {'items': 'At least one line is required.'}
    for i, req in enumerate(requested):
        if not isinstance(req, dict):
            errors[f'items[{i}]'] = 'Each line must be an object.'
            continue
        try:
            idx = int(req.get('invoice_line_index'))
        except (TypeError, ValueError):
            errors[f'items[{i}].invoice_line_index'] = 'invoice_line_index is required.'
            continue
        if idx < 0 or idx >= len(invoice_lines):
            errors[f'items[{i}].invoice_line_index'] = f'Invoice has no line {idx}.'
            continue
        src = invoice_lines[idx]
        src_qty = max(0.0, _num(src.get('quantity')))
        src_rate = max(0.0, _num(src.get('rate')))
        qty = _num(req.get('quantity')) if req.get('quantity') is not None else src_qty
        rate = _num(req.get('rate')) if req.get('rate') is not None else src_rate
        discount = max(0.0, _num(req.get('discount')))
        if qty <= 0:
            errors[f'items[{i}].quantity'] = 'Quantity must be greater than 0.'
        elif qty > src_qty + _EPS:
            errors[f'items[{i}].quantity'] = f'Quantity exceeds the invoiced {src_qty:g}.'
        if rate < 0:
            errors[f'items[{i}].rate'] = 'Rate cannot be negative.'
        elif rate > src_rate + _EPS:
            errors[f'items[{i}].rate'] = f'Rate exceeds the invoiced {src_rate:.2f}.'
        taxable = max(0.0, qty * rate - discount)
        if taxable <= 0 and not errors.get(f'items[{i}].quantity') and not errors.get(f'items[{i}].rate'):
            errors[f'items[{i}].rate'] = 'Line value must be greater than zero.'
        if errors:
            continue
        tax_rate = max(0.0, _num(src.get('tax')))
        cgst, sgst, igst = tax_split(taxable, tax_rate, tax_type)
        code, unit = _line_hsn(src, products_by_name)
        lines.append({
            'invoice_line_index': idx,
            'name': src.get('name') or src.get('product_name') or src.get('item_name') or '',
            'product_id': src.get('product_id'),
            'hsn_sac': code,
            'unit': unit,
            'quantity': qty,
            'rate': rate,
            'discount': discount,
            'tax': tax_rate,
            'taxable_value': _r2(taxable),
            'cgst': cgst,
            'sgst': sgst,
            'igst': igst,
            'amount': _r2(taxable + cgst + sgst + igst),
        })
    return lines, errors


def note_totals(lines, round_off=0.0):
    subtotal = _r2(sum(_num(l.get('taxable_value')) for l in lines))
    cgst = _r2(sum(_num(l.get('cgst')) for l in lines))
    sgst = _r2(sum(_num(l.get('sgst')) for l in lines))
    igst = _r2(sum(_num(l.get('igst')) for l in lines))
    total_tax = _r2(cgst + sgst + igst)
    return {
        'subtotal': subtotal,
        'cgst_amount': cgst,
        'sgst_amount': sgst,
        'igst_amount': igst,
        'total_tax': total_tax,
        'round_off': _r2(_num(round_off)),
        'total_amount': _r2(subtotal + total_tax + _num(round_off)),
    }


# ── Limits: never credit more than was invoiced ───────────────────────────────

def is_live(note):
    return str(note.get('status') or 'Issued') != 'Cancelled'


def credited_so_far(notes):
    """What earlier live credit notes already credited: per invoice line and per head."""
    per_line = {}
    heads = {'subtotal': 0.0, 'cgst_amount': 0.0, 'sgst_amount': 0.0, 'igst_amount': 0.0,
             'total_amount': 0.0}
    for note in notes:
        if not is_live(note) or note.get('note_type') != 'credit':
            continue
        for k in heads:
            heads[k] += _num(note.get(k))
        for line in note.get('items') or []:
            idx = line.get('invoice_line_index')
            if idx is None:
                continue
            row = per_line.setdefault(int(idx), {'quantity': 0.0, 'taxable_value': 0.0})
            row['quantity'] += _num(line.get('quantity'))
            row['taxable_value'] += _num(line.get('taxable_value'))
    return per_line, heads


def limit_errors(invoice, lines, totals, existing_notes):
    """Errors ({field: message}) when [lines]/[totals] would credit more than the
    invoice carried, counting earlier live credit notes on it."""
    errors = {}
    per_line, heads = credited_so_far(existing_notes)
    invoice_lines = [it for it in (invoice.get('items') or []) if isinstance(it, dict)]
    # Per line: quantity and taxable value.
    asked = {}
    for i, line in enumerate(lines):
        idx = int(line['invoice_line_index'])
        row = asked.setdefault(idx, {'quantity': 0.0, 'taxable_value': 0.0, 'first': i})
        row['quantity'] += _num(line.get('quantity'))
        row['taxable_value'] += _num(line.get('taxable_value'))
    for idx, row in asked.items():
        src = invoice_lines[idx]
        done = per_line.get(idx, {'quantity': 0.0, 'taxable_value': 0.0})
        qty_left = max(0.0, _num(src.get('quantity')) - done['quantity'])
        value_left = max(0.0, line_taxable(src) - done['taxable_value'])
        if row['quantity'] > qty_left + _EPS:
            errors[f"items[{row['first']}].quantity"] = (
                f"Only {qty_left:g} of line {idx} is left to credit "
                f"({done['quantity']:g} already credited).")
        if row['taxable_value'] > value_left + _EPS:
            errors[f"items[{row['first']}].rate"] = (
                f"Only {value_left:.2f} of line {idx}'s value is left to credit "
                f"({done['taxable_value']:.2f} already credited).")
    # Per tax head and in total, against what the invoice actually carried.
    invoiced = {
        'subtotal': sum(line_taxable(it) for it in invoice_lines),
        'cgst_amount': _num(invoice.get('cgst_amount')),
        'sgst_amount': _num(invoice.get('sgst_amount')),
        'igst_amount': _num(invoice.get('igst_amount')),
        'total_amount': _num(invoice.get('total_amount')),
    }
    labels = {'subtotal': 'taxable value', 'cgst_amount': 'CGST', 'sgst_amount': 'SGST',
              'igst_amount': 'IGST', 'total_amount': 'total'}
    for k, label in labels.items():
        if heads[k] + _num(totals.get(k)) > invoiced[k] + _EPS:
            errors[k] = (f'Credit {label} {heads[k] + _num(totals.get(k)):.2f} would exceed '
                         f'the invoiced {label} {invoiced[k]:.2f} '
                         f'({heads[k]:.2f} already credited).')
    return errors


# ── Effect on the invoice ─────────────────────────────────────────────────────

def invoice_balance(invoice):
    """What is still owed: total, plus debit notes, less credits applied and payments."""
    balance = (_num(invoice.get('total_amount')) + _num(invoice.get('debit_applied'))
               - _num(invoice.get('credit_applied')) - _num(invoice.get('amount_paid')))
    return max(0.0, _r2(balance))


def status_for_balance(invoice, balance):
    """The invoice status once [balance] is what remains, keeping Draft/Cancelled as they are."""
    status = str(invoice.get('status') or '')
    if status in ('Draft', 'Cancelled'):
        return status
    if balance <= 0:
        return 'Paid'
    if status == 'Paid':
        return 'Partially Paid' if _num(invoice.get('amount_paid')) > 0 else 'Issued'
    return status


def apply_note(invoice, note, apply_to_invoice=True):
    """Return (updated invoice, amount applied) for a newly issued note.

    A credit note reduces the balance by as much of its total as the invoice
    still owes; the rest stays on the note as an unapplied credit to refund.
    A debit note raises the balance by its total.
    """
    updated = dict(invoice)
    total = _num(note.get('total_amount'))
    applied = 0.0
    if note.get('note_type') == 'debit':
        updated['debit_applied'] = _r2(_num(invoice.get('debit_applied')) + total)
        updated['debit_notes_total'] = _r2(_num(invoice.get('debit_notes_total')) + total)
        applied = total
    else:
        updated['credit_notes_total'] = _r2(_num(invoice.get('credit_notes_total')) + total)
        if apply_to_invoice:
            applied = _r2(min(total, invoice_balance(invoice)))
            updated['credit_applied'] = _r2(_num(invoice.get('credit_applied')) + applied)
    balance = invoice_balance(updated)
    updated['balance_due'] = balance
    updated['status'] = status_for_balance(updated, balance)
    return updated, applied


def reverse_note(invoice, note):
    """Undo [note]'s effect on the invoice (the note was cancelled)."""
    updated = dict(invoice)
    total = _num(note.get('total_amount'))
    if note.get('note_type') == 'debit':
        updated['debit_applied'] = _r2(max(0.0, _num(invoice.get('debit_applied')) - total))
        updated['debit_notes_total'] = _r2(max(0.0, _num(invoice.get('debit_notes_total')) - total))
    else:
        updated['credit_notes_total'] = _r2(max(0.0, _num(invoice.get('credit_notes_total')) - total))
        updated['credit_applied'] = _r2(max(0.0, _num(invoice.get('credit_applied'))
                                            - _num(note.get('applied_to_invoice'))))
    balance = invoice_balance(updated)
    updated['balance_due'] = balance
    updated['status'] = status_for_balance(updated, balance)
    return updated


# ── Rule 53(1A) and Section 34(2) warnings ───────────────────────────────────

def note_issues(note, customer, products_by_name, annual_return_date=None):
    """[{'field', 'problem'}] for what a credit/debit note misses under Rule 53(1A),
    plus the Section 34(2) time limit for a credit note. Warnings only."""
    customer = customer or {}
    issues = []

    def flag(field, problem):
        issues.append({'field': field, 'problem': problem})

    if not NOTE_NUMBER_RE.match(str(note.get('note_number') or '')):
        flag('note_number', 'Note number must be 1-16 characters: letters, digits, "-" or "/" only.')
    if not note.get('invoice_number') or not parse_iso_date(note.get('invoice_date')):
        flag('invoice_number', 'The original invoice number and date are required (Rule 53(1A)(h)).')
    if note.get('reason') not in REASONS:
        flag('reason', 'A reason is required: ' + ', '.join(sorted(REASONS)) + '.')

    gstin = str(customer.get('gst_number') or customer.get('gstin') or '').strip().upper()
    treatment = note.get('gst_treatment') or customer.get('gst_treatment')
    unregistered = treatment in ('unregistered', 'consumer')
    registered = treatment in REGISTERED_TREATMENTS
    if gstin and not unregistered and not validate_gstin_format(gstin):
        flag('customer.gst_number', f'Customer GSTIN {gstin} is not a valid GSTIN.')
    elif not gstin and registered:
        flag('customer.gst_number', 'Registered (B2B) recipient: the recipient GSTIN is missing.')
    if (unregistered or (not gstin and not registered)) and not _has_address(customer):
        flag('customer.billing_address',
             'Unregistered recipient: name, address and state are required (Rule 53(1A)(f)).')

    if note.get('is_gst_applicable') and not state_code(note.get('place_of_supply')):
        flag('place_of_supply', 'Place of supply (state) is missing or not a known state.')

    for idx, item in enumerate(note.get('items') or []):
        if not isinstance(item, dict):
            continue
        code, _ = _line_hsn(item, products_by_name)
        if note.get('is_gst_applicable') and not _HSN_RE.match(code):
            flag(f'items[{idx}].hsn_sac',
                 f"Line '{item.get('name', '')}': HSN/SAC code is missing or invalid (4-8 digits).")

    if note.get('note_type') == 'credit' and note.get('is_gst_applicable'):
        late = section34_issue(parse_iso_date(note.get('note_date')),
                               parse_iso_date(note.get('invoice_date')), annual_return_date)
        if late:
            issues.append(late)
    return issues

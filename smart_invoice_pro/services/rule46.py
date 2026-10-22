"""
Rule 46 (CGST Rules) field checks for a tax invoice.

Returns warnings, never errors: an invoice that misses a Rule 46 field is still
saved, and the caller shows the list so the user can fix it before filing.
Drafts and invoices without GST are not tax invoices yet, so they get no checks.

Pure function: the caller passes the customer and the tenant's products
(for HSN codes looked up by line name, as in the GSTR-1 export).
"""
import re

from smart_invoice_pro.api.gst_api import validate_gstin_format
from smart_invoice_pro.services.gstr1 import state_code, _line_hsn, _HSN_RE

# Rule 46(b): at most 16 characters, letters, digits, '-' and '/' only.
INVOICE_NUMBER_RE = re.compile(r'^[A-Za-z0-9/-]{1,16}$')

# Rule 46(e): unregistered buyer, taxable value of Rs 50,000 or more needs name and address.
UNREGISTERED_ADDRESS_LIMIT = 50000.0

# Treatments whose buyer is GST-registered, so Rule 46(e) needs the buyer's GSTIN.
REGISTERED_TREATMENTS = {'regular', 'composition', 'special_economic_zone', 'deemed_export'}


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _taxable_value(invoice):
    """Value of the supply before tax: subtotal less the invoice-level discount."""
    if invoice.get('subtotal') is not None:
        return _num(invoice.get('subtotal')) - _num(invoice.get('invoice_discount'))
    return _num(invoice.get('total_amount')) - _num(invoice.get('total_tax'))


def _has_address(customer):
    return any(str(customer.get(k) or '').strip()
               for k in ('billing_address', 'billing_street', 'billing_city'))


def rule46_issues(invoice, customer, products_by_name):
    """Return [{'field': ..., 'problem': ...}] for the Rule 46 fields this invoice misses.

    products_by_name  {lower-cased product name: product document}, for HSN codes
    """
    if not invoice.get('is_gst_applicable') or invoice.get('status') in ('Draft', 'Cancelled'):
        return []
    customer = customer or {}
    issues = []

    def flag(field, problem):
        issues.append({'field': field, 'problem': problem})

    number = str(invoice.get('invoice_number') or '')
    if not INVOICE_NUMBER_RE.match(number):
        flag('invoice_number', 'Invoice number must be 1-16 characters: letters, digits, "-" or "/" only.')

    gstin = str(customer.get('gst_number') or customer.get('gstin') or '').strip().upper()
    treatment = invoice.get('gst_treatment')
    unregistered = treatment in ('unregistered', 'consumer')
    registered = treatment in REGISTERED_TREATMENTS
    if gstin and not unregistered and not validate_gstin_format(gstin):
        flag('customer.gst_number', f'Customer GSTIN {gstin} is not a valid GSTIN.')
    elif not gstin and registered:
        flag('customer.gst_number', 'Registered (B2B) buyer: the buyer GSTIN is missing.')
    # The buyer name is already required by validate_invoice_payload.
    if (unregistered or (not gstin and not registered)) \
            and _taxable_value(invoice) >= UNREGISTERED_ADDRESS_LIMIT and not _has_address(customer):
        flag('customer.billing_address',
             'Unregistered buyer, value Rs 50,000 or more: the buyer address is required.')

    if not state_code(invoice.get('place_of_supply')):
        flag('place_of_supply', 'Place of supply (state) is missing or not a known state.')

    for idx, item in enumerate(invoice.get('items') or []):
        if not isinstance(item, dict):
            continue
        code, _ = _line_hsn(item, products_by_name)
        if not _HSN_RE.match(code):
            flag(f'items[{idx}].hsn_sac',
                 f"Line '{item.get('name', '')}': HSN/SAC code is missing or invalid (4-8 digits).")
    return issues

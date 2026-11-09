"""
GSTR-1 Table 9B (CDNR / CDNUR) and the netting of notes into B2CS and the HSN
summary (services/gstr1.py), plus the credit-note part of GET /api/reports/gstr1.

Golden tests: each asserts the full table the offline tool would import.
All GSTINs, names and amounts are made-up example data.
"""
from unittest.mock import patch

from smart_invoice_pro.services.credit_notes import build_note_lines, note_totals
from smart_invoice_pro.services.gstr1 import build_gstr1
from tests.conftest import TENANT_A
from tests.test_gstr1 import BUYER_DL, BUYER_PB, CUSTOMERS, PRODUCTS, SELLER, _inv, _line


def _invoice(id_, number, customer, pos, items, total, date='2026-09-15', **extra):
    return {'id': id_, **_inv(number, customer, pos, items, total, date=date, **extra)}


def _note(number, invoice, lines, note_type='credit', date='2026-09-20', **extra):
    """A note built the way the API builds it: lines priced off the invoice."""
    tax_type = 'IGST' if any(float(it.get('igst') or 0) > 0 for it in invoice['items']) or \
        invoice['place_of_supply'] != 'Punjab' else 'CGST_SGST'
    items, errors = build_note_lines(invoice, lines, PRODUCTS, tax_type)
    assert errors == {}, errors
    return {'id': f'id-{number}', 'note_type': note_type, 'note_number': number, 'note_date': date,
            'status': 'Issued', 'is_gst_applicable': True, 'invoice_id': invoice['id'],
            'invoice_number': invoice['invoice_number'], 'customer_id': invoice['customer_id'],
            'items': items, **note_totals(items), **extra}


def _build(invoices, notes, originals=None):
    stats = {}
    payload, issues, count = build_gstr1(
        invoices, CUSTOMERS, PRODUCTS, SELLER, 2026, 9, credit_notes=notes,
        invoices_by_id={i['id']: i for i in (originals or invoices)}, stats=stats)
    return payload, issues, count, stats['notes']


# Registered buyer, intra-state: 10 brackets @ 18% and installation @ 18%.
INV_PB = _invoice('i-pb', 'INV-1', 'c-pb', 'Punjab',
                  [_line('Steel Bracket', 10, 100, 18, cgst=90, sgst=90, igst=0),
                   _line('Installation', 1, 500, 18, cgst=45, sgst=45, igst=0)], 1770)
# Registered buyer, inter-state.
INV_DL = _invoice('i-dl', 'INV-2', 'c-dl', 'Delhi', [_line('Steel Bracket', 2, 1000, 12, igst=240, cgst=0, sgst=0)], 2240)
# Unregistered, inter-state, above the B2CL limit.
INV_B2CL = _invoice('i-b2cl', 'INV-3', 'c-retail', 'Delhi', [_line('Steel Bracket', 1, 100000, 18, igst=18000, cgst=0, sgst=0)], 118000)
# Unregistered, intra-state: B2CS.
INV_B2CS = _invoice('i-b2cs', 'INV-5', 'c-retail', 'Punjab', [_line('Steel Bracket', 1, 1000, 18, cgst=90, sgst=90, igst=0)], 1180)


class TestCdnr:
    def test_partial_credit_intra_state_registered(self):
        note = _note('CN-1', INV_PB, [{'invoice_line_index': 0, 'quantity': 4}])
        payload, issues, count, notes = _build([INV_PB], [note])
        assert issues == [] and count == 1 and notes == 1
        assert payload['cdnr'] == [{'ctin': BUYER_PB, 'nt': [{
            'ntty': 'C', 'nt_num': 'CN-1', 'nt_dt': '20-09-2026', 'p_gst': 'N', 'val': 472.0,
            'pos': '03', 'rchrg': 'N', 'inv_typ': 'R',
            'itms': [{'num': 1, 'itm_det': {'rt': 18.0, 'txval': 400.0, 'iamt': 0.0, 'camt': 36.0,
                                            'samt': 36.0, 'csamt': 0}}]}]}]
        assert 'cdnur' not in payload
        # The invoice itself is still reported in full in B2B.
        assert payload['b2b'][0]['inv'][0]['itms'][0]['itm_det']['txval'] == 1500.0

    def test_full_credit_inter_state_registered_uses_igst(self):
        note = _note('CN-2', INV_DL, [{'invoice_line_index': 0}])
        payload, issues, _, _ = _build([INV_DL], [note])
        assert issues == []
        nt = payload['cdnr'][0]['nt'][0]
        assert payload['cdnr'][0]['ctin'] == BUYER_DL and nt['pos'] == '07' and nt['val'] == 2240.0
        assert nt['itms'][0]['itm_det'] == {'rt': 12.0, 'txval': 2000.0, 'iamt': 240.0, 'camt': 0.0,
                                            'samt': 0.0, 'csamt': 0}

    def test_debit_note_is_type_d(self):
        note = _note('DN-1', INV_PB, [{'invoice_line_index': 1}], note_type='debit')
        payload, _, _, _ = _build([INV_PB], [note])
        assert payload['cdnr'][0]['nt'][0]['ntty'] == 'D'
        assert payload['cdnr'][0]['nt'][0]['val'] == 590.0

    def test_several_notes_on_one_invoice_each_get_a_row(self):
        notes = [_note('CN-3', INV_PB, [{'invoice_line_index': 0, 'quantity': 3}]),
                 _note('CN-4', INV_PB, [{'invoice_line_index': 0, 'quantity': 7}], date='2026-09-25'),
                 _note('CN-5', INV_PB, [{'invoice_line_index': 1}], date='2026-09-28')]
        payload, issues, _, count = _build([INV_PB], notes)
        assert issues == [] and count == 3
        rows = payload['cdnr'][0]['nt']
        assert [(r['nt_num'], r['nt_dt'], r['val']) for r in rows] == [
            ('CN-3', '20-09-2026', 354.0), ('CN-4', '25-09-2026', 826.0), ('CN-5', '28-09-2026', 590.0)]

    def test_original_invoice_from_an_earlier_month(self):
        august = _invoice('i-aug', 'INV-A', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 1000, 18, cgst=90, sgst=90, igst=0)], 1180, date='2026-08-10')
        note = _note('CN-6', august, [{'invoice_line_index': 0}])
        payload, issues, count, notes = _build([], [note], originals=[august])
        assert count == 0 and notes == 1
        assert 'b2b' not in payload
        assert payload['cdnr'][0]['nt'][0]['nt_num'] == 'CN-6'
        # No sales of that HSN this month, so the netted HSN row is negative and flagged.
        assert [i['problem'][:21] for i in issues] == ['HSN 73269099 at 18%: ']
        assert payload['hsn']['hsn_b2b'][0]['txval'] == -1000.0

    def test_rounding_per_line_to_paise(self):
        inv = _invoice('i-r', 'INV-R', 'c-pb', 'Punjab', [_line('Steel Bracket', 3, 33.33, 18, cgst=9.0, sgst=9.0, igst=0)], 118)
        # 1 × 33.33 @ 18% → 2.9997 each half → 3.00 each.
        note = _note('CN-7', inv, [{'invoice_line_index': 0, 'quantity': 1}])
        payload, _, _, _ = _build([inv], [note])
        det = payload['cdnr'][0]['nt'][0]['itms'][0]['itm_det']
        assert (det['txval'], det['camt'], det['samt']) == (33.33, 3.0, 3.0)
        assert payload['cdnr'][0]['nt'][0]['val'] == 39.33


class TestCdnur:
    def test_note_against_a_b2cl_invoice(self):
        note = _note('CN-8', INV_B2CL, [{'invoice_line_index': 0, 'rate': 20000}])
        payload, issues, _, _ = _build([INV_B2CL], [note])
        assert issues == []
        assert payload['cdnur'] == [{
            'typ': 'B2CL', 'ntty': 'C', 'nt_num': 'CN-8', 'nt_dt': '20-09-2026', 'p_gst': 'N',
            'val': 23600.0, 'pos': '07',
            'itms': [{'num': 1, 'itm_det': {'rt': 18.0, 'txval': 20000.0, 'iamt': 3600.0, 'csamt': 0}}]}]
        assert 'cdnr' not in payload
        assert payload['b2cl'][0]['inv'][0]['val'] == 118000.0

    def test_the_note_value_does_not_decide_the_table_the_original_does(self):
        # A small note against a B2CL invoice is still CDNUR, not B2CS.
        note = _note('CN-9', INV_B2CL, [{'invoice_line_index': 0, 'rate': 100}])
        payload, _, _, _ = _build([INV_B2CL], [note])
        assert payload['cdnur'][0]['val'] == 118.0
        assert 'b2cs' not in payload


class TestB2csNetting:
    def test_note_against_a_b2cs_invoice_is_netted_not_listed(self):
        other = _invoice('i-b2cs2', 'INV-6', 'c-retail', 'Punjab', [_line('Steel Bracket', 2, 500, 18, cgst=90, sgst=90, igst=0)], 1180)
        note = _note('CN-10', INV_B2CS, [{'invoice_line_index': 0, 'rate': 400}])   # 400 + 72 tax
        payload, issues, _, notes = _build([INV_B2CS, other], [note])
        assert issues == [] and notes == 1
        assert 'cdnur' not in payload and 'cdnr' not in payload
        assert payload['b2cs'] == [{'sply_ty': 'INTRA', 'pos': '03', 'typ': 'OE', 'rt': 18.0,
                                    'txval': 1600.0, 'iamt': 0.0, 'camt': 144.0, 'samt': 144.0, 'csamt': 0}]

    def test_debit_note_adds_to_b2cs(self):
        note = _note('DN-2', INV_B2CS, [{'invoice_line_index': 0, 'rate': 100}], note_type='debit')
        payload, _, _, _ = _build([INV_B2CS], [note])
        assert (payload['b2cs'][0]['txval'], payload['b2cs'][0]['camt']) == (1100.0, 99.0)

    def test_credit_exceeding_the_months_b2cs_sales_is_flagged(self):
        august = _invoice('i-aug2', 'INV-7', 'c-retail', 'Punjab', [_line('Steel Bracket', 1, 5000, 18, cgst=450, sgst=450, igst=0)], 5900, date='2026-08-01')
        note = _note('CN-11', august, [{'invoice_line_index': 0}])
        payload, issues, _, _ = _build([INV_B2CS], [note], originals=[INV_B2CS, august])
        assert payload['b2cs'][0]['txval'] == -4000.0
        assert [i['problem'][:40] for i in issues if i['invoice'] is None] == [
            'B2CS intra-state, place of supply 03, 18', 'HSN 73269099 at 18%: net of credit notes']


class TestHsnNetting:
    def test_hsn_summary_nets_notes_by_code_and_rate(self):
        note = _note('CN-12', INV_PB, [{'invoice_line_index': 0, 'quantity': 4}, {'invoice_line_index': 1}])
        payload, issues, _, _ = _build([INV_PB], [note])
        assert issues == []
        rows = payload['hsn']['hsn_b2b']
        assert [(r['hsn_sc'], r['uqc'], r['qty'], r['txval'], r['camt'], r['samt']) for r in rows] == [
            ('73269099', 'NOS', 6.0, 600.0, 54.0, 54.0),
            ('998719', 'NA', 0.0, 0.0, 0.0, 0.0)]

    def test_b2c_notes_net_into_hsn_b2c(self):
        note = _note('CN-13', INV_B2CS, [{'invoice_line_index': 0, 'rate': 250}])
        payload, _, _, _ = _build([INV_B2CS], [note])
        row = payload['hsn']['hsn_b2c'][0]
        assert (row['qty'], row['txval'], row['camt']) == (0.0, 750.0, 67.5)


class TestFilteringAndIssues:
    def test_cancelled_other_month_and_non_gst_notes_are_skipped(self):
        notes = [_note('X1', INV_PB, [{'invoice_line_index': 1}], status='Cancelled'),
                 _note('X2', INV_PB, [{'invoice_line_index': 1}], date='2026-10-01'),
                 _note('X3', INV_PB, [{'invoice_line_index': 1}], is_gst_applicable=False)]
        payload, issues, _, count = _build([INV_PB], notes)
        assert count == 0 and issues == [] and 'cdnr' not in payload

    def test_late_credit_note_is_reported_and_left_out(self):
        old = _invoice('i-old', 'INV-OLD', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 1000, 18, cgst=90, sgst=90, igst=0)], 1180, date='2025-02-10')
        note = _note('CN-14', old, [{'invoice_line_index': 0}])   # dated 20-09-2026, deadline was 30-11-2025
        payload, issues, _, count = _build([], [note], originals=[old])
        assert count == 0 and 'cdnr' not in payload
        assert issues[0]['invoice'] == 'CN-14' and 'Section 34(2)' in issues[0]['problem']
        # A debit note has no such limit.
        debit = _note('DN-3', old, [{'invoice_line_index': 0}], note_type='debit')
        payload, issues, _, count = _build([], [debit], originals=[old])
        assert issues == [] and count == 1 and payload['cdnr'][0]['nt'][0]['ntty'] == 'D'

    def test_missing_original_bad_number_and_export_original_are_reported(self):
        export = _invoice('i-exp', 'INV-E', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 100, 0)], 100, gst_treatment='export')
        notes = [_note('CN-15', INV_PB, [{'invoice_line_index': 1}], invoice_id='nope'),
                 _note('A note with spaces', INV_PB, [{'invoice_line_index': 1}]),
                 _note('CN-16', export, [{'invoice_line_index': 0}])]
        _, issues, _, count = _build([INV_PB], notes, originals=[INV_PB, export])
        problems = {i['invoice']: i['problem'] for i in issues}
        assert 'not found' in problems['CN-15']
        assert problems['A note with spaces'].startswith('Note number must be')
        assert 'export/SEZ' in problems['CN-16']
        assert count == 1   # the badly numbered note is still exported

    def test_note_whose_split_contradicts_the_place_of_supply_is_reported(self):
        note = _note('CN-17', INV_PB, [{'invoice_line_index': 1}])
        note['items'][0].update({'cgst': 0, 'sgst': 0, 'igst': 90})
        _, issues, _, count = _build([INV_PB], [note])
        assert count == 0 and 'taxed as IGST' in issues[0]['problem']


class TestEndpoint:
    def test_loads_notes_and_their_originals_from_earlier_months(self, client, headers_a):
        august = _invoice('i-aug', 'INV-A', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 1000, 18, cgst=90, sgst=90, igst=0)], 1180, date='2026-08-10')
        note = _note('CN-20', august, [{'invoice_line_index': 0}])
        with patch('smart_invoice_pro.api.reports_api.invoices_container') as mock_inv, \
             patch('smart_invoice_pro.api.reports_api.credit_notes_container') as mock_notes, \
             patch('smart_invoice_pro.api.reports_api.customers_container') as mock_cust, \
             patch('smart_invoice_pro.api.reports_api.products_container') as mock_prod, \
             patch('smart_invoice_pro.api.reports_api.settings_container') as mock_set:
            mock_inv.query_items.side_effect = [[INV_PB], [august]]
            mock_notes.query_items.return_value = [note]
            mock_cust.query_items.return_value = list(CUSTOMERS.values())
            mock_prod.query_items.return_value = list(PRODUCTS.values())
            mock_set.query_items.return_value = [{'gstin': SELLER}]
            resp = client.get('/api/reports/gstr1?month=2026-09', headers=headers_a)
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['invoice_count'] == 1 and body['note_count'] == 1 and body['issues'] == []
        assert body['gstr1']['cdnr'][0]['nt'][0]['nt_num'] == 'CN-20'
        # Second invoice query fetched exactly the missing original, tenant-scoped.
        params = mock_inv.query_items.call_args.kwargs['parameters']
        assert {'name': '@tid', 'value': TENANT_A} in params
        assert {'name': '@ids', 'value': ['i-aug']} in params
        note_params = mock_notes.query_items.call_args.kwargs['parameters']
        assert {'name': '@month', 'value': '2026-09'} in note_params

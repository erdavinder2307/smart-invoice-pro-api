"""
Tests for the GSTR-1 builder (services/gstr1.py) and GET /api/reports/gstr1.

All GSTINs, names and amounts are made-up example data.
"""
from unittest.mock import patch

import pytest

from smart_invoice_pro.services.gstr1 import B2CL_LIMIT, build_gstr1, state_code
from tests.conftest import TENANT_A

SELLER = '03AAACS1234A1Z1'          # Punjab
BUYER_PB = '03BBBCS5678B1Z2'        # registered, Punjab (intra-state)
BUYER_DL = '07CCCCS9012C1Z3'        # registered, Delhi (inter-state)

CUSTOMERS = {
    'c-pb': {'id': 'c-pb', 'gst_number': BUYER_PB, 'gst_treatment': 'regular'},
    'c-dl': {'id': 'c-dl', 'gst_number': BUYER_DL, 'gst_treatment': 'regular'},
    'c-retail': {'id': 'c-retail', 'gst_treatment': 'consumer', 'billing_state': 'Punjab'},
}
PRODUCTS = {
    'steel bracket': {'name': 'Steel Bracket', 'hsn_sac': '73269099', 'unit': 'nos'},
    'installation': {'name': 'Installation', 'hsn_sac': '998719'},
}


def _inv(number, customer, pos, items, total, date='2026-09-15', status='Issued', **extra):
    return {'invoice_number': number, 'customer_id': customer, 'place_of_supply': pos,
            'issue_date': date, 'status': status, 'is_gst_applicable': True,
            'gst_treatment': CUSTOMERS[customer]['gst_treatment'], 'items': items,
            'total_amount': total, **extra}


def _line(name, qty, rate, tax, **extra):
    return {'name': name, 'quantity': qty, 'rate': rate, 'discount': 0, 'tax': tax, **extra}


def _build(invoices):
    return build_gstr1(invoices, CUSTOMERS, PRODUCTS, SELLER, 2026, 9)


class TestStateCode:
    @pytest.mark.parametrize('value,code', [('Punjab', '03'), ('delhi', '07'), ('3', '03'),
                                            ('27', '27'), ('Atlantis', ''), ('', '')])
    def test_names_and_numbers(self, value, code):
        assert state_code(value) == code


class TestTables:
    def test_registered_buyer_goes_to_b2b_with_rate_rows(self):
        inv = _inv('INV-1', 'c-pb', 'Punjab',
                   [_line('Steel Bracket', 10, 100, 18, cgst=90, sgst=90, igst=0),
                    _line('Installation', 1, 500, 18, cgst=45, sgst=45, igst=0)], 1770)
        payload, issues, count = _build([inv])
        assert issues == [] and count == 1
        assert payload['gstin'] == SELLER and payload['fp'] == '092026'
        entry = payload['b2b'][0]
        assert entry['ctin'] == BUYER_PB
        doc = entry['inv'][0]
        assert doc == {'inum': 'INV-1', 'idt': '15-09-2026', 'val': 1770.0, 'pos': '03',
                       'rchrg': 'N', 'inv_typ': 'R',
                       'itms': [{'num': 1, 'itm_det': {'rt': 18.0, 'txval': 1500.0, 'iamt': 0.0,
                                                       'camt': 135.0, 'samt': 135.0, 'csamt': 0}}]}
        assert 'b2cs' not in payload and 'b2cl' not in payload

    def test_inter_state_registered_buyer_gets_igst(self):
        inv = _inv('INV-2', 'c-dl', 'Delhi', [_line('Steel Bracket', 2, 1000, 12)], 2240)
        payload, _, _ = _build([inv])
        det = payload['b2b'][0]['inv'][0]['itms'][0]['itm_det']
        assert payload['b2b'][0]['inv'][0]['pos'] == '07'
        assert (det['iamt'], det['camt'], det['samt']) == (240.0, 0.0, 0.0)

    def test_large_inter_state_consumer_sale_goes_to_b2cl(self):
        inv = _inv('INV-3', 'c-retail', 'Delhi', [_line('Steel Bracket', 1, 100000, 18)], 118000)
        payload, _, _ = _build([inv])
        assert payload['b2cl'] == [{'pos': '07', 'inv': [{
            'inum': 'INV-3', 'idt': '15-09-2026', 'val': 118000.0,
            'itms': [{'num': 1, 'itm_det': {'rt': 18.0, 'txval': 100000.0, 'iamt': 18000.0,
                                            'camt': 0.0, 'samt': 0.0, 'csamt': 0}}]}]}]
        assert 'b2cs' not in payload

    def test_b2cl_limit_is_strictly_above_one_lakh(self):
        inv = _inv('INV-4', 'c-retail', 'Delhi', [_line('Steel Bracket', 1, B2CL_LIMIT, 0)], B2CL_LIMIT)
        payload, _, _ = _build([inv])
        assert 'b2cl' not in payload
        assert payload['b2cs'][0]['sply_ty'] == 'INTER'

    def test_small_consumer_sales_are_summed_in_b2cs(self):
        invs = [
            _inv('INV-5', 'c-retail', 'Punjab', [_line('Steel Bracket', 1, 1000, 18)], 1180),
            _inv('INV-6', 'c-retail', 'Punjab', [_line('Steel Bracket', 2, 500, 18)], 1180),
        ]
        payload, _, count = _build(invs)
        assert count == 2
        assert payload['b2cs'] == [{'sply_ty': 'INTRA', 'pos': '03', 'typ': 'OE', 'rt': 18.0,
                                    'txval': 2000.0, 'iamt': 0.0, 'camt': 180.0, 'samt': 180.0,
                                    'csamt': 0}]


class TestHsn:
    def test_hsn_is_split_into_b2b_and_b2c(self):
        invs = [
            _inv('INV-7', 'c-pb', 'Punjab', [_line('Steel Bracket', 10, 100, 18)], 1180),
            _inv('INV-8', 'c-retail', 'Punjab', [_line('Steel Bracket', 3, 100, 18),
                                                 _line('Installation', 1, 500, 18)], 944),
        ]
        payload, issues, _ = _build(invs)
        assert issues == []
        b2b, b2c = payload['hsn']['hsn_b2b'], payload['hsn']['hsn_b2c']
        assert [(r['hsn_sc'], r['uqc'], r['qty'], r['txval']) for r in b2b] == [('73269099', 'NOS', 10.0, 1000.0)]
        # services (SAC 99…) carry no quantity and UQC "NA"
        assert [(r['hsn_sc'], r['uqc'], r['qty'], r['txval']) for r in b2c] == [
            ('73269099', 'NOS', 3.0, 300.0), ('998719', 'NA', 0.0, 500.0)]

    def test_line_hsn_wins_over_product_hsn(self):
        inv = _inv('INV-9', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 100, 18, hsn_sac='73181500')], 118)
        payload, _, _ = _build([inv])
        assert payload['hsn']['hsn_b2b'][0]['hsn_sc'] == '73181500'

    def test_missing_hsn_is_reported_not_guessed(self):
        inv = _inv('INV-10', 'c-pb', 'Punjab', [_line('Unknown widget', 1, 100, 18)], 118)
        payload, issues, _ = _build([inv])
        assert issues == [{'invoice': 'INV-10', 'problem': "Line 'Unknown widget': HSN/SAC code is missing or invalid."}]
        assert payload['hsn']['hsn_b2b'][0]['hsn_sc'] == 'MISSING'


class TestFiltering:
    def test_skips_drafts_cancelled_other_months_and_non_gst(self):
        line = [_line('Steel Bracket', 1, 100, 18)]
        invs = [
            _inv('D', 'c-pb', 'Punjab', line, 118, status='Draft'),
            _inv('C', 'c-pb', 'Punjab', line, 118, status='Cancelled'),
            _inv('O', 'c-pb', 'Punjab', line, 118, date='2026-08-31'),
            _inv('N', 'c-pb', 'Punjab', line, 118, is_gst_applicable=False),
            _inv('A', 'c-pb', 'Punjab', line, 118, lifecycle_status='ARCHIVED'),
        ]
        payload, issues, count = _build(invs)
        assert count == 0 and issues == []
        assert 'b2b' not in payload

    def test_export_and_missing_pos_and_bad_gstin_are_reported(self):
        customers = {**CUSTOMERS, 'c-bad': {'id': 'c-bad', 'gst_number': 'NOTAGSTIN', 'gst_treatment': 'regular'}}
        line = [_line('Steel Bracket', 1, 100, 18)]
        invs = [
            {**_inv('E', 'c-pb', 'Punjab', line, 100), 'gst_treatment': 'export'},
            _inv('P', 'c-retail', '', line, 118),
            {**_inv('G', 'c-pb', 'Punjab', line, 118), 'customer_id': 'c-bad'},
        ]
        customers['c-retail'] = {'id': 'c-retail', 'gst_treatment': 'consumer'}
        payload, issues, count = build_gstr1(invs, customers, PRODUCTS, SELLER, 2026, 9)
        problems = {i['invoice']: i['problem'] for i in issues}
        assert problems['E'].startswith('Export/SEZ supply')
        assert problems['P'] == 'Place of supply is missing.'
        assert problems['G'].startswith('Customer GSTIN NOTAGSTIN')
        assert count == 1 and payload['b2cs'][0]['pos'] == '03'

    def test_missing_seller_gstin_is_reported(self):
        _, issues, _ = build_gstr1([], CUSTOMERS, PRODUCTS, '', 2026, 9)
        assert issues == [{'invoice': None, 'problem': 'Organisation GSTIN is missing or invalid.'}]


class TestEndpoint:
    def test_requires_month(self, client, headers_a):
        resp = client.get('/api/reports/gstr1', headers=headers_a)
        assert resp.status_code == 400

    def test_returns_gstr1_for_the_tenant(self, client, headers_a):
        inv = _inv('INV-1', 'c-pb', 'Punjab', [_line('Steel Bracket', 1, 100, 18)], 118)
        with patch('smart_invoice_pro.api.reports_api.invoices_container') as mock_inv, \
             patch('smart_invoice_pro.api.reports_api.customers_container') as mock_cust, \
             patch('smart_invoice_pro.api.reports_api.products_container') as mock_prod, \
             patch('smart_invoice_pro.api.reports_api.settings_container') as mock_set:
            mock_inv.query_items.return_value = [inv]
            mock_cust.query_items.return_value = list(CUSTOMERS.values())
            mock_prod.query_items.return_value = list(PRODUCTS.values())
            mock_set.query_items.return_value = [{'gstin': SELLER}]
            resp = client.get('/api/reports/gstr1?month=2026-09', headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['invoice_count'] == 1 and body['issues'] == []
        assert body['gstr1']['b2b'][0]['ctin'] == BUYER_PB
        params = mock_inv.query_items.call_args.kwargs['parameters']
        assert {'name': '@tid', 'value': TENANT_A} in params
        assert {'name': '@month', 'value': '2026-09'} in params

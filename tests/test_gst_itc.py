"""
Tests for the input-tax summary (services/gst_itc.py) and GET /api/reports/gst-itc.

All GSTINs, names and amounts are made-up example data.
"""
from unittest.mock import patch

from smart_invoice_pro.services.gst_itc import build_itc_summary
from tests.conftest import TENANT_A

BUYER = '03AAACS1234A1Z1'           # the organisation, Punjab
VENDOR_PB = '03DDDCS3456D1Z4'       # registered, Punjab (intra-state)
VENDOR_DL = '07EEECS7890E1Z5'       # registered, Delhi (inter-state)

VENDORS = {
    'v-pb': {'id': 'v-pb', 'vendor_name': 'Example Supplies', 'gst_number': VENDOR_PB},
    'v-dl': {'id': 'v-dl', 'vendor_name': 'Example Traders', 'gst_number': VENDOR_DL},
    'v-local': {'id': 'v-local', 'vendor_name': 'Corner Shop', 'gst_number': ''},
    'v-bad': {'id': 'v-bad', 'vendor_name': 'Typo Vendor', 'gst_number': '03XYZ'},
}


def _bill(number, vendor, items, total, date='2026-09-10', status='Unpaid', **extra):
    return {'bill_number': number, 'vendor_id': vendor, 'bill_date': date,
            'payment_status': status, 'items': items, 'total_amount': total, **extra}


def _line(qty, rate, tax):
    return {'name': 'Item', 'quantity': qty, 'rate': rate, 'tax': tax}


def _build(bills):
    return build_itc_summary(bills, VENDORS, BUYER, 2026, 9)


class TestItc:
    def test_intra_state_vendor_splits_cgst_sgst(self):
        summary, issues, count = _build([_bill('B-1', 'v-pb', [_line(10, 100, 18)], 1180)])
        assert issues == [] and count == 1
        assert summary['gstin'] == BUYER and summary['fp'] == '092026'
        entry = summary['b2b'][0]
        assert entry['ctin'] == VENDOR_PB
        doc = entry['inv'][0]
        assert doc['inum'] == 'B-1' and doc['idt'] == '10-09-2026' and doc['val'] == 1180
        assert doc['itms'] == [{'num': 1, 'itm_det': {'rt': 18.0, 'txval': 1000.0, 'iamt': 0.0,
                                                       'camt': 90.0, 'samt': 90.0, 'csamt': 0}}]
        assert summary['itc']['total'] == {'txval': 1000.0, 'iamt': 0.0, 'camt': 90.0, 'samt': 90.0}

    def test_inter_state_vendor_is_igst(self):
        summary, _, _ = _build([_bill('B-2', 'v-dl', [_line(2, 500, 12)], 1120)])
        assert summary['itc']['total'] == {'txval': 1000.0, 'iamt': 120.0, 'camt': 0.0, 'samt': 0.0}

    def test_itc_summed_by_rate_across_bills(self):
        summary, _, count = _build([
            _bill('B-1', 'v-pb', [_line(1, 1000, 18), _line(1, 200, 5)], 1390),
            _bill('B-2', 'v-dl', [_line(1, 500, 18)], 590),
        ])
        assert count == 2
        assert summary['itc']['by_rate'] == [
            {'rt': 5.0, 'txval': 200.0, 'iamt': 0.0, 'camt': 5.0, 'samt': 5.0},
            {'rt': 18.0, 'txval': 1500.0, 'iamt': 90.0, 'camt': 90.0, 'samt': 90.0},
        ]
        assert [e['ctin'] for e in summary['b2b']] == [VENDOR_PB, VENDOR_DL]

    def test_unregistered_vendor_gives_no_credit(self):
        summary, issues, count = _build([_bill('B-3', 'v-local', [_line(1, 1000, 18)], 1180)])
        assert count == 1
        assert summary['b2b'] == [] and summary['itc']['total']['txval'] == 0
        assert summary['unregistered'] == {'txval': 1000.0, 'iamt': 0.0, 'camt': 90.0, 'samt': 90.0}
        assert issues == [{'bill': 'B-3', 'problem': 'Vendor has no GSTIN: no input tax credit.'}]

    def test_invalid_vendor_gstin_is_flagged_not_credited(self):
        summary, issues, _ = _build([_bill('B-4', 'v-bad', [_line(1, 100, 18)], 118)])
        assert summary['b2b'] == []
        assert issues[0] == {'bill': 'B-4', 'problem':
                             'Vendor GSTIN 03XYZ is not a valid GSTIN; no input tax credit taken.'}

    def test_other_months_drafts_and_archived_are_left_out(self):
        summary, issues, count = _build([
            _bill('B-5', 'v-pb', [_line(1, 100, 18)], 118, date='2026-08-31'),
            _bill('B-6', 'v-pb', [_line(1, 100, 18)], 118, status='Draft'),
            _bill('B-7', 'v-pb', [_line(1, 100, 18)], 118, lifecycle_status='ARCHIVED'),
        ])
        assert count == 0 and issues == [] and summary['b2b'] == []

    def test_bill_without_lines_is_an_issue(self):
        summary, issues, count = _build([_bill('B-8', 'v-pb', [], 118)])
        assert count == 0 and summary['b2b'] == []
        assert issues == [{'bill': 'B-8', 'problem':
                           'Bill has no item lines; its tax cannot be split by rate.'}]

    def test_missing_organisation_gstin_is_reported(self):
        _, issues, _ = build_itc_summary([], VENDORS, '', 2026, 9)
        assert issues == [{'bill': None, 'problem': 'Organisation GSTIN is missing or invalid.'}]


class TestEndpoint:
    def test_requires_month(self, client, headers_a):
        resp = client.get('/api/reports/gst-itc', headers=headers_a)
        assert resp.status_code == 400

    def test_returns_itc_for_the_tenant(self, client, headers_a):
        bill = _bill('B-1', 'v-pb', [_line(1, 100, 18)], 118)
        with patch('smart_invoice_pro.api.reports_api.bills_container') as mock_bills, \
             patch('smart_invoice_pro.api.reports_api.vendors_container') as mock_vend, \
             patch('smart_invoice_pro.api.reports_api.settings_container') as mock_set:
            mock_bills.query_items.return_value = [bill]
            mock_vend.query_items.return_value = list(VENDORS.values())
            mock_set.query_items.return_value = [{'gstin': BUYER}]
            resp = client.get('/api/reports/gst-itc?month=2026-09', headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['bill_count'] == 1 and body['issues'] == []
        assert body['itc']['b2b'][0]['ctin'] == VENDOR_PB
        params = mock_bills.query_items.call_args.kwargs['parameters']
        assert {'name': '@tid', 'value': TENANT_A} in params
        assert {'name': '@month', 'value': '2026-09'} in params

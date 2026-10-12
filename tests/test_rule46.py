"""
Tests for the Rule 46 invoice checks (services/rule46.py) and the
gst_warnings field on invoice create/update.

All GSTINs, names and amounts are made-up example data.
"""
from unittest.mock import patch, MagicMock

from smart_invoice_pro.services.rule46 import rule46_issues

PRODUCTS = {'steel bracket': {'name': 'Steel Bracket', 'hsn_sac': '73269099'}}
REGISTERED = {'id': 'c-1', 'gst_number': '03BBBCS5678B1Z2', 'gst_treatment': 'regular'}
CONSUMER = {'id': 'c-2', 'gst_treatment': 'consumer'}


def _inv(**extra):
    return {'invoice_number': 'INV/26-27/0001', 'status': 'Issued', 'is_gst_applicable': True,
            'gst_treatment': 'regular', 'place_of_supply': 'Punjab', 'total_amount': 1180,
            'items': [{'name': 'Steel Bracket', 'quantity': 1, 'rate': 1000, 'tax': 18}], **extra}


def _fields(issues):
    return [i['field'] for i in issues]


class TestRule46:
    def test_complete_invoice_has_no_issues(self):
        assert rule46_issues(_inv(), REGISTERED, PRODUCTS) == []

    def test_drafts_and_non_gst_invoices_are_not_checked(self):
        bad = {'invoice_number': 'a very long invoice number', 'place_of_supply': ''}
        assert rule46_issues(_inv(status='Draft', **bad), REGISTERED, {}) == []
        assert rule46_issues(_inv(is_gst_applicable=False, **bad), REGISTERED, {}) == []

    def test_invoice_number_rules(self):
        assert _fields(rule46_issues(_inv(invoice_number='INV/2026-27/000001'), REGISTERED, PRODUCTS)) == ['invoice_number']
        assert _fields(rule46_issues(_inv(invoice_number='INV #1'), REGISTERED, PRODUCTS)) == ['invoice_number']

    def test_invalid_buyer_gstin_and_unknown_place_of_supply(self):
        cust = {**REGISTERED, 'gst_number': 'NOTAGSTIN'}
        assert _fields(rule46_issues(_inv(place_of_supply='Atlantis'), cust, PRODUCTS)) == \
            ['customer.gst_number', 'place_of_supply']

    def test_missing_hsn_is_reported_per_line(self):
        inv = _inv(items=[{'name': 'Steel Bracket', 'quantity': 1, 'rate': 10, 'tax': 18},
                          {'name': 'Mystery part', 'quantity': 1, 'rate': 10, 'tax': 18}])
        assert _fields(rule46_issues(inv, REGISTERED, PRODUCTS)) == ['items[1].hsn_sac']

    def test_unregistered_buyer_needs_address_from_fifty_thousand(self):
        inv = _inv(gst_treatment='consumer', total_amount=50000)
        assert _fields(rule46_issues(inv, CONSUMER, PRODUCTS)) == ['customer.billing_address']
        assert rule46_issues(inv, {**CONSUMER, 'billing_city': 'Mohali'}, PRODUCTS) == []
        assert rule46_issues(_inv(gst_treatment='consumer', total_amount=49999), CONSUMER, PRODUCTS) == []


class TestInvoiceApiWarnings:
    @patch("smart_invoice_pro.api.invoices.products_container")
    @patch("smart_invoice_pro.api.invoices._get_customer_state", return_value=('Punjab', 'regular', 'Punjab'))
    @patch("smart_invoice_pro.api.invoices._get_seller_state", return_value='Punjab')
    @patch("smart_invoice_pro.api.invoices.must_suppress_sales_tax", return_value=False)
    @patch("smart_invoice_pro.api.invoices.customers_container")
    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_create_returns_warnings_without_storing_them(self, mock_inv, mock_gc, mock_cust, _sup, _ss, _cs,
                                                          mock_prod, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        mock_cust.query_items.return_value = [REGISTERED]
        mock_prod.query_items.return_value = []        # no product HSN codes
        payload = {**sample_invoice, 'status': 'Issued', 'is_gst_applicable': True, 'place_of_supply': 'Punjab',
                   'items': [{'name': 'Consulting', 'quantity': 1, 'rate': 1000, 'discount': 0, 'tax': 18}]}
        with patch("smart_invoice_pro.api.invoices._adjust_stock", return_value=(None, None)):
            resp = client.post("/api/invoices", json=payload, headers=headers_a)
        assert resp.status_code == 201
        assert _fields(resp.get_json()['gst_warnings']) == ['items[0].hsn_sac']
        assert 'gst_warnings' not in mock_inv.create_item.call_args[1]['body']

    @patch("smart_invoice_pro.api.invoices.customers_container")
    @patch("smart_invoice_pro.api.invoices.get_container")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_draft_create_has_empty_warnings(self, mock_inv, mock_gc, mock_cust, client, headers_a, sample_invoice):
        mock_gc.return_value = MagicMock()
        mock_cust.query_items.return_value = [{"id": "cust-001"}]
        payload = {**sample_invoice, 'items': [{'name': 'Consulting', 'quantity': 1, 'rate': 100, 'tax': 0}]}
        resp = client.post("/api/invoices", json=payload, headers=headers_a)
        assert resp.status_code == 201
        assert resp.get_json()['gst_warnings'] == []

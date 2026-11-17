"""
Credit and debit notes (services/credit_notes.py and /api/credit-notes).

All GSTINs, names and amounts are made-up example data.
"""
import copy
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from smart_invoice_pro.services.credit_notes import (
    apply_note, build_note_lines, invoice_balance, invoice_tax_type, limit_errors,
    note_issues, note_totals, reverse_note, section34_deadline, section34_issue,
    status_for_balance, tax_split,
)
from tests.conftest import TENANT_A, TENANT_B, auth_headers

PRODUCTS = {'steel bracket': {'name': 'Steel Bracket', 'hsn_sac': '73269099', 'unit': 'nos'}}
REGISTERED = {'id': 'c-1', 'gst_number': '03BBBCS5678B1Z2', 'gst_treatment': 'regular',
              'billing_city': 'Mohali'}
CONSUMER = {'id': 'c-2', 'gst_treatment': 'consumer'}


def _invoice(**extra):
    """An intra-state (Punjab) invoice: 10 × 100 @ 18% and 1 × 500 @ 12%."""
    inv = {
        'id': 'inv-1', 'tenant_id': TENANT_A, 'invoice_number': 'INV-001', 'customer_id': 'c-1',
        'customer_name': 'Acme Corp', 'issue_date': '2026-09-15', 'status': 'Issued',
        'is_gst_applicable': True, 'place_of_supply': 'Punjab', 'gst_treatment': 'regular',
        'items': [
            {'name': 'Steel Bracket', 'quantity': 10, 'rate': 100, 'discount': 0, 'tax': 18,
             'cgst': 90, 'sgst': 90, 'igst': 0},
            {'name': 'Installation', 'quantity': 1, 'rate': 500, 'discount': 0, 'tax': 12,
             'cgst': 30, 'sgst': 30, 'igst': 0, 'hsn_sac': '998719'},
        ],
        'subtotal': 1500, 'cgst_amount': 120, 'sgst_amount': 120, 'igst_amount': 0,
        'total_tax': 240, 'total_amount': 1740, 'amount_paid': 0, 'balance_due': 1740,
    }
    inv.update(extra)
    return inv


def _note(lines, note_type='credit', **extra):
    totals = note_totals(lines)
    return {'note_type': note_type, 'status': 'Issued', 'items': lines, **totals, **extra}


class TestTaxOnNote:
    def test_follows_the_invoice_split_and_rates(self):
        lines, errors = build_note_lines(_invoice(), [{'invoice_line_index': 0, 'quantity': 4},
                                                      {'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')
        assert errors == {}
        assert [(l['quantity'], l['rate'], l['tax'], l['taxable_value'], l['cgst'], l['sgst'], l['igst'])
                for l in lines] == [(4.0, 100.0, 18.0, 400.0, 36.0, 36.0, 0.0),
                                    (1.0, 500.0, 12.0, 500.0, 30.0, 30.0, 0.0)]
        assert lines[0]['hsn_sac'] == '73269099' and lines[1]['hsn_sac'] == '998719'
        assert note_totals(lines) == {'subtotal': 900.0, 'cgst_amount': 66.0, 'sgst_amount': 66.0,
                                      'igst_amount': 0.0, 'total_tax': 132.0, 'round_off': 0.0,
                                      'total_amount': 1032.0}

    def test_inter_state_invoice_gives_igst(self):
        lines, _ = build_note_lines(_invoice(), [{'invoice_line_index': 0, 'quantity': 1}], PRODUCTS, 'IGST')
        assert (lines[0]['cgst'], lines[0]['sgst'], lines[0]['igst']) == (0.0, 0.0, 18.0)

    def test_rounding_matches_calculate_gst(self):
        # 0.14 @ 18%: each half 0.0126 -> 0.01; IGST 0.0252 -> 0.03.
        assert tax_split(0.14, 18, 'CGST_SGST') == (0.01, 0.01, 0.0)
        assert tax_split(0.14, 18, 'IGST') == (0.0, 0.0, 0.03)
        assert tax_split(999.99, 18, 'CGST_SGST') == (90.0, 90.0, 0.0)

    def test_tax_type_is_read_from_the_invoice(self):
        assert invoice_tax_type(_invoice()) == 'CGST_SGST'
        assert invoice_tax_type(_invoice(cgst_amount=0, sgst_amount=0, igst_amount=240,
                                         items=[{'tax': 18, 'igst': 180}])) == 'IGST'
        assert invoice_tax_type(_invoice(is_gst_applicable=False)) == 'NONE'
        # A 0%-only invoice is placed by its place of supply against the seller.
        zero = _invoice(cgst_amount=0, sgst_amount=0, igst_amount=0, items=[{'tax': 0}])
        assert invoice_tax_type(zero, seller_state='Punjab') == 'CGST_SGST'
        assert invoice_tax_type(zero, seller_state='Delhi') == 'IGST'

    def test_partial_credit_per_unit_discount(self):
        # Post-sale discount of 10 per unit on the 10 brackets.
        lines, errors = build_note_lines(_invoice(), [{'invoice_line_index': 0, 'rate': 10}], PRODUCTS, 'CGST_SGST')
        assert errors == {}
        assert (lines[0]['quantity'], lines[0]['taxable_value'], lines[0]['cgst']) == (10.0, 100.0, 9.0)

    def test_rejects_more_than_invoiced_or_bad_lines(self):
        _, errors = build_note_lines(_invoice(), [
            {'invoice_line_index': 0, 'quantity': 11},
            {'invoice_line_index': 1, 'rate': 600},
            {'invoice_line_index': 5},
            {'invoice_line_index': 0, 'quantity': 0},
        ], PRODUCTS, 'CGST_SGST')
        assert set(errors) == {'items[0].quantity', 'items[1].rate', 'items[2].invoice_line_index',
                               'items[3].quantity'}
        assert build_note_lines(_invoice(), [], PRODUCTS, 'CGST_SGST')[1] == {'items': 'At least one line is required.'}


class TestLimits:
    def _lines(self, *reqs):
        lines, errors = build_note_lines(_invoice(), list(reqs), PRODUCTS, 'CGST_SGST')
        assert errors == {}
        return lines

    def test_full_credit_of_everything_is_allowed_once(self):
        lines = self._lines({'invoice_line_index': 0}, {'invoice_line_index': 1})
        assert limit_errors(_invoice(), lines, note_totals(lines), []) == {}

    def test_second_note_cannot_exceed_what_is_left(self):
        first = _note(self._lines({'invoice_line_index': 0, 'quantity': 6}))
        second = self._lines({'invoice_line_index': 0, 'quantity': 5})
        errors = limit_errors(_invoice(), second, note_totals(second), [first])
        assert 'items[0].quantity' in errors and 'Only 4' in errors['items[0].quantity']
        ok = self._lines({'invoice_line_index': 0, 'quantity': 4})
        assert limit_errors(_invoice(), ok, note_totals(ok), [first]) == {}

    def test_cancelled_and_debit_notes_do_not_count(self):
        cancelled = _note(self._lines({'invoice_line_index': 0}), status='Cancelled')
        debit = _note(self._lines({'invoice_line_index': 0}), note_type='debit')
        lines = self._lines({'invoice_line_index': 0})
        assert limit_errors(_invoice(), lines, note_totals(lines), [cancelled, debit]) == {}

    def test_per_head_cap_against_the_invoice_amounts(self):
        # The invoice document says CGST 100, less than its lines add up to.
        inv = _invoice(cgst_amount=100)
        lines = self._lines({'invoice_line_index': 0}, {'invoice_line_index': 1})
        errors = limit_errors(inv, lines, note_totals(lines), [])
        assert list(errors) == ['cgst_amount']
        assert 'exceed the invoiced CGST 100.00' in errors['cgst_amount']

    def test_value_cap_per_line_with_a_per_unit_discount_note(self):
        first = _note(self._lines({'invoice_line_index': 0, 'rate': 60}))   # 600 of 1000 credited
        second = self._lines({'invoice_line_index': 0, 'rate': 50})         # 500 more: too much
        errors = limit_errors(_invoice(), second, note_totals(second), [first])
        assert 'Only 400.00' in errors['items[0].rate']


class TestInvoiceEffect:
    def test_credit_reduces_the_balance_and_pays_off_the_invoice(self):
        note = _note(build_note_lines(_invoice(), [{'invoice_line_index': 0}, {'invoice_line_index': 1}],
                                      PRODUCTS, 'CGST_SGST')[0])
        updated, applied = apply_note(_invoice(), note)
        assert applied == 1740.0
        assert (updated['balance_due'], updated['status'], updated['credit_applied']) == (0.0, 'Paid', 1740.0)

    def test_credit_on_a_paid_invoice_stays_unapplied(self):
        paid = _invoice(amount_paid=1740, balance_due=0, status='Paid')
        note = _note(build_note_lines(paid, [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0])
        updated, applied = apply_note(paid, note)
        assert applied == 0.0
        assert updated['status'] == 'Paid' and updated['balance_due'] == 0.0
        assert updated['credit_notes_total'] == 560.0

    def test_partial_credit_then_payment_keeps_the_credit(self):
        note = _note(build_note_lines(_invoice(), [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0])
        updated, applied = apply_note(_invoice(), note)
        assert (applied, updated['balance_due']) == (560.0, 1180.0)
        # The next payment is measured against total less the credit, not total alone.
        assert invoice_balance({**updated, 'amount_paid': 1180.0}) == 0.0
        assert invoice_balance({**updated, 'amount_paid': 1000.0}) == 180.0

    def test_debit_note_raises_the_balance_and_reopens_a_paid_invoice(self):
        paid = _invoice(amount_paid=1740, balance_due=0, status='Paid')
        note = _note(build_note_lines(paid, [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0],
                     note_type='debit')
        updated, applied = apply_note(paid, note)
        assert (applied, updated['balance_due'], updated['status']) == (560.0, 560.0, 'Partially Paid')

    def test_cancelling_reverses_exactly(self):
        inv = _invoice()
        note = _note(build_note_lines(inv, [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0])
        updated, applied = apply_note(inv, note)
        note['applied_to_invoice'] = applied
        back = reverse_note(updated, note)
        assert (back['balance_due'], back['status'], back['credit_applied'], back['credit_notes_total']) == \
            (1740.0, 'Issued', 0.0, 0.0)

    def test_status_rules(self):
        assert status_for_balance({'status': 'Draft'}, 0) == 'Draft'
        assert status_for_balance({'status': 'Cancelled'}, 0) == 'Cancelled'
        assert status_for_balance({'status': 'Overdue'}, 0) == 'Paid'
        assert status_for_balance({'status': 'Overdue'}, 10) == 'Overdue'
        assert status_for_balance({'status': 'Paid', 'amount_paid': 0}, 10) == 'Issued'


class TestSection34TimeLimit:
    @pytest.mark.parametrize('invoice_date,deadline', [
        (date(2026, 9, 15), date(2027, 11, 30)),   # FY 2026-27 ends 31 Mar 2027
        (date(2026, 3, 31), date(2026, 11, 30)),   # last day of FY 2025-26
        (date(2026, 4, 1), date(2027, 11, 30)),    # first day of FY 2026-27
    ])
    def test_deadline_is_30_november_after_the_financial_year(self, invoice_date, deadline):
        assert section34_deadline(invoice_date) == deadline

    def test_annual_return_filed_earlier_brings_the_deadline_forward(self):
        assert section34_deadline(date(2026, 9, 15), annual_return_date=date(2027, 10, 1)) == date(2027, 10, 1)
        assert section34_deadline(date(2026, 9, 15), annual_return_date=date(2027, 12, 25)) == date(2027, 11, 30)

    def test_warning_only_after_the_deadline(self):
        assert section34_issue(date(2027, 11, 30), date(2026, 9, 15)) is None
        late = section34_issue(date(2027, 12, 1), date(2026, 9, 15))
        assert late['field'] == 'note_date' and 'Section 34(2)' in late['problem'] and '30-11-2027' in late['problem']


class TestRule53Warnings:
    def _good(self, **extra):
        lines, _ = build_note_lines(_invoice(), [{'invoice_line_index': 0}], PRODUCTS, 'CGST_SGST')
        return {'note_type': 'credit', 'note_number': 'CN-00001', 'note_date': '2026-09-20',
                'invoice_number': 'INV-001', 'invoice_date': '2026-09-15', 'reason': 'sales_return',
                'gst_treatment': 'regular', 'is_gst_applicable': True, 'place_of_supply': 'Punjab',
                'items': lines, **extra}

    def test_complete_note_has_no_warnings(self):
        assert note_issues(self._good(), REGISTERED, PRODUCTS) == []

    def test_each_rule_53_field(self):
        note = self._good(note_number='CN/2026-27/000001', reason='', invoice_date='', place_of_supply='Atlantis')
        note['items'][0]['hsn_sac'] = ''
        issues = note_issues(note, {**REGISTERED, 'gst_number': ''}, {})
        assert [i['field'] for i in issues] == ['note_number', 'invoice_number', 'reason',
                                                'customer.gst_number', 'place_of_supply', 'items[0].hsn_sac']

    def test_unregistered_recipient_needs_an_address(self):
        issues = note_issues(self._good(gst_treatment='consumer'), CONSUMER, PRODUCTS)
        assert [i['field'] for i in issues] == ['customer.billing_address']
        assert note_issues(self._good(gst_treatment='consumer'), {**CONSUMER, 'billing_city': 'Mohali'}, PRODUCTS) == []

    def test_late_credit_note_is_a_warning_not_a_block(self):
        issues = note_issues(self._good(note_date='2027-12-01'), REGISTERED, PRODUCTS)
        assert [i['field'] for i in issues] == ['note_date']
        # A debit note has no Section 34(2) limit.
        assert note_issues(self._good(note_date='2027-12-01', note_type='debit'), REGISTERED, PRODUCTS) == []


# ── API ──────────────────────────────────────────────────────────────────────

def _post_note(client, headers, mock_inv, mock_notes, mock_cust, body=None, invoice=None, existing=()):
    mock_inv.query_items.return_value = [copy.deepcopy(invoice or _invoice())]
    mock_notes.query_items.return_value = list(existing)
    mock_cust.query_items.return_value = [REGISTERED]
    payload = {'invoice_id': 'inv-1', 'note_date': '2026-09-20', 'reason': 'sales_return',
               'items': [{'invoice_line_index': 1}]}
    payload.update(body or {})
    return client.post('/api/credit-notes', json=payload, headers=headers)


@patch('smart_invoice_pro.api.credit_notes_api.customers_container')
@patch('smart_invoice_pro.api.credit_notes_api.credit_notes_container')
@patch('smart_invoice_pro.api.credit_notes_api.invoices_container')
class TestCreateEndpoint:
    def test_issues_a_note_and_reduces_the_invoice_balance(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust)
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        assert body['note_number'] == 'CN-00001' and body['note_type'] == 'credit'
        assert body['tenant_id'] == TENANT_A and body['status'] == 'Issued'
        assert (body['invoice_number'], body['invoice_date']) == ('INV-001', '2026-09-15')
        assert (body['subtotal'], body['cgst_amount'], body['sgst_amount'], body['total_amount']) == (500.0, 30.0, 30.0, 560.0)
        assert body['applied_to_invoice'] == 560.0 and body['unapplied_amount'] == 0.0
        assert body['invoice'] == {'id': 'inv-1', 'balance_due': 1180.0, 'status': 'Issued'}
        assert body['gst_warnings'] == []
        saved = mock_notes.create_item.call_args.kwargs['body']
        assert saved['customer_gstin'] == REGISTERED['gst_number'] and saved['tax_type'] == 'CGST_SGST'
        invoice = mock_inv.replace_item.call_args.kwargs['body']
        assert (invoice['balance_due'], invoice['credit_applied'], invoice['credit_notes_total']) == (1180.0, 560.0, 560.0)

    def test_reports_rule_53_warnings_without_blocking(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust,
                          body={'note_date': '2027-12-01', 'items': [{'invoice_line_index': 0}]})
        assert resp.status_code == 201
        fields = [w['field'] for w in resp.get_json()['gst_warnings']]
        # The bracket line carries no HSN here (no product lookup), and the note is late.
        assert fields == ['items[0].hsn_sac', 'note_date']

    def test_validation_errors(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust,
                          body={'reason': 'because', 'note_type': 'refund', 'note_date': 'yesterday'})
        assert resp.status_code == 400
        assert set(resp.get_json()['details']) == {'reason', 'note_type', 'note_date'}

    def test_note_before_the_invoice_date_is_refused(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust, body={'note_date': '2026-09-01'})
        assert resp.status_code == 400 and 'note_date' in resp.get_json()['details']

    def test_only_against_an_issued_invoice(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust, invoice=_invoice(status='Draft'))
        assert resp.status_code == 400 and 'invoice_id' in resp.get_json()['details']

    def test_cap_across_notes_is_enforced(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        earlier = _note(build_note_lines(_invoice(), [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0])
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust, existing=[earlier])
        assert resp.status_code == 400
        assert 'items[0].quantity' in resp.get_json()['details']
        mock_notes.create_item.assert_not_called()

    def test_debit_note_raises_the_balance(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust,
                          body={'note_type': 'debit', 'reason': 'value_correction'})
        assert resp.status_code == 201
        assert resp.get_json()['note_number'] == 'DN-00001'
        assert resp.get_json()['invoice']['balance_due'] == 2300.0

    def test_unapplied_credit_on_a_paid_invoice(self, mock_inv, mock_notes, mock_cust, client, headers_a):
        paid = _invoice(amount_paid=1740, balance_due=0, status='Paid')
        resp = _post_note(client, headers_a, mock_inv, mock_notes, mock_cust, invoice=paid)
        body = resp.get_json()
        assert body['applied_to_invoice'] == 0.0 and body['unapplied_amount'] == 560.0

    def test_invoice_lookup_is_scoped_to_the_callers_tenant(self, mock_inv, mock_notes, mock_cust, client):
        resp = _post_note(client, auth_headers(user_id='u-b', tenant_id=TENANT_B), mock_inv, mock_notes, mock_cust)
        # The query is tenant-scoped; the mock ignores it, so check the parameters instead.
        params = mock_inv.query_items.call_args.kwargs['parameters']
        assert {'name': '@tid', 'value': TENANT_B} in params
        assert resp.status_code == 201  # the mock returned the invoice regardless


@patch('smart_invoice_pro.api.credit_notes_api.credit_notes_container')
@patch('smart_invoice_pro.api.credit_notes_api.invoices_container')
class TestCancelRefundDelete:
    def _stored_note(self, **extra):
        inv = _invoice()
        note = _note(build_note_lines(inv, [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')[0])
        updated, applied = apply_note(inv, note)
        note.update({'id': 'cn-1', 'tenant_id': TENANT_A, 'note_number': 'CN-00001', 'invoice_id': 'inv-1',
                     'applied_to_invoice': applied, 'refunded_amount': 0.0, **extra})
        return note, updated

    def test_cancel_reverses_the_invoice_and_keeps_the_note(self, mock_inv, mock_notes, client, headers_a):
        note, invoice = self._stored_note()
        mock_notes.query_items.return_value = [note]
        mock_inv.query_items.return_value = [invoice]
        resp = client.post('/api/credit-notes/cn-1/cancel', json={'reason': 'Raised in error'}, headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'Cancelled' and resp.get_json()['cancel_reason'] == 'Raised in error'
        assert mock_inv.replace_item.call_args.kwargs['body']['balance_due'] == 1740.0
        mock_notes.replace_item.assert_called_once()
        mock_notes.delete_item.assert_not_called()

    def test_cancel_needs_a_reason_and_only_once(self, mock_inv, mock_notes, client, headers_a):
        note, _ = self._stored_note(status='Cancelled')
        mock_notes.query_items.return_value = [note]
        assert client.post('/api/credit-notes/cn-1/cancel', json={}, headers=headers_a).status_code == 400
        assert client.post('/api/credit-notes/cn-1/cancel', json={'reason': 'x'}, headers=headers_a).status_code == 409

    def test_delete_is_refused(self, mock_inv, mock_notes, client, headers_a):
        resp = client.delete('/api/credit-notes/cn-1', headers=headers_a)
        assert resp.status_code == 409 and 'never deleted' in resp.get_json()['error']
        mock_notes.delete_item.assert_not_called()

    def test_refund_of_unapplied_credit(self, mock_inv, mock_notes, client, headers_a):
        note, _ = self._stored_note(applied_to_invoice=200.0)
        mock_notes.query_items.return_value = [note]
        resp = client.post('/api/credit-notes/cn-1/refund', headers=headers_a,
                           json={'amount': 300, 'payment_mode': 'Bank Transfer', 'payment_date': '2026-09-25'})
        assert resp.status_code == 200
        body = resp.get_json()['credit_note']
        assert (body['refunded_amount'], body['unapplied_amount']) == (300.0, 60.0)
        too_much = client.post('/api/credit-notes/cn-1/refund', headers=headers_a,
                               json={'amount': 100, 'payment_mode': 'Cash', 'payment_date': '2026-09-26'})
        assert too_much.status_code == 400

    def test_get_and_list_are_tenant_scoped(self, mock_inv, mock_notes, client, headers_a):
        note, _ = self._stored_note()
        mock_notes.query_items.return_value = [note]
        resp = client.get('/api/credit-notes/cn-1', headers=headers_a)
        assert resp.status_code == 200 and resp.get_json()['note_number'] == 'CN-00001'
        assert {'name': '@tid', 'value': TENANT_A} in mock_notes.query_items.call_args.kwargs['parameters']
        mock_notes.query_items.side_effect = [[note], [1]]
        resp = client.get('/api/credit-notes?invoice_id=inv-1', headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()['total'] == 1 and resp.get_json()['items'][0]['id'] == 'cn-1'
        params = mock_notes.query_items.call_args.kwargs['parameters']
        assert {'name': '@invoice_id', 'value': 'inv-1'} in params


class TestPaymentAfterCreditNote:
    @patch('smart_invoice_pro.api.invoices.invoices_container')
    def test_record_payment_keeps_the_credit_applied(self, mock_inv, client, headers_a):
        # 1740 invoiced, 560 credited: 1180 due. Paying 1180 settles it; the balance
        # must not spring back to total - paid = 560.
        inv = _invoice(credit_applied=560.0, balance_due=1180.0)
        mock_inv.query_items.return_value = [inv]
        resp = client.post('/api/invoices/inv-1/record-payment', headers=headers_a,
                           json={'amount': 1180, 'payment_mode': 'Cash', 'payment_date': '2026-09-30'})
        assert resp.status_code == 200, resp.get_json()
        saved = mock_inv.replace_item.call_args.kwargs['body']
        assert (saved['balance_due'], saved['status']) == (0.0, 'Paid')


class TestNoteNumberSeries:
    @patch('smart_invoice_pro.api.invoice_preferences_api.settings_container')
    def test_notes_have_their_own_counters(self, mock_ctr):
        from smart_invoice_pro.api.invoice_preferences_api import claim_next_number, peek_next_number
        mock_ctr.query_items.return_value = [{'id': f'{TENANT_A}:invoice_preferences', 'tenant_id': TENANT_A,
                                              'next_invoice_number': 42, 'next_credit_note_number': 7,
                                              'credit_note_prefix': 'CRN/', 'number_padding': 3}]
        assert peek_next_number(TENANT_A, 'invoice') == 'INV-042'
        assert peek_next_number(TENANT_A, 'credit_note') == 'CRN/007'
        assert peek_next_number(TENANT_A, 'debit_note') == 'DN-001'
        assert claim_next_number(TENANT_A, 'credit_note') == 'CRN/007'
        saved = mock_ctr.create_item.call_args.kwargs['body']
        assert saved['next_credit_note_number'] == 8 and saved['next_invoice_number'] == 42


class TestPdf:
    @patch('smart_invoice_pro.api.credit_notes_api.credit_notes_container')
    def test_pdf_is_headed_credit_note(self, mock_notes, client, headers_a):
        inv = _invoice()
        lines, _ = build_note_lines(inv, [{'invoice_line_index': 1}], PRODUCTS, 'CGST_SGST')
        note = {'id': 'cn-1', 'tenant_id': TENANT_A, 'note_type': 'credit', 'note_number': 'CN-00001',
                'note_date': '2026-09-20', 'status': 'Issued', 'reason': 'sales_return', 'reason_note': 'Damaged',
                'invoice_number': 'INV-001', 'invoice_date': '2026-09-15', 'customer_name': 'Acme Corp',
                'is_gst_applicable': True, 'place_of_supply': 'Punjab', 'items': lines, **note_totals(lines)}
        mock_notes.query_items.return_value = [note]
        resp = client.get('/api/credit-notes/cn-1/pdf', headers=headers_a)
        assert resp.status_code == 200, resp.get_json()
        assert resp.headers['Content-Type'] == 'application/pdf'
        assert resp.data[:4] == b'%PDF'
        assert 'credit_note_CN-00001.pdf' in resp.headers['Content-Disposition']

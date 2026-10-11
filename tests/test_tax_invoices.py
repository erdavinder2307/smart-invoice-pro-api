"""
Tests for GST tax invoices on subscription charges (utils/tax_invoices.py and the billing endpoints):
tax split and rounding, financial-year numbering (including the 31 Mar → 1 Apr rollover and two
concurrent webhooks), buyer state, the webhook hook, the list and the PDF download.
Every name, GSTIN and id here is a dummy value.
"""
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from azure.cosmos import exceptions

from smart_invoice_pro.utils import tax_invoices
from tests.conftest import TENANT_A, TENANT_B, auth_headers
from tests.test_billing import BILLING_ENV, _send, _tenant


@pytest.fixture()
def billing_env(monkeypatch):
    for name, value in BILLING_ENV.items():
        monkeypatch.setenv(name, value)


class FakeCounters:
    """Just enough of a Cosmos container for the counter: ETag-checked replace."""

    def __init__(self, before_replace=None):
        self.docs = {}
        self.lock = threading.Lock()
        self.before_replace = before_replace
        self.conflicts = 0

    def read_item(self, item, partition_key):
        with self.lock:
            if item not in self.docs:
                raise exceptions.CosmosResourceNotFoundError(status_code=404, message="missing")
            return dict(self.docs[item])

    def create_item(self, body):
        with self.lock:
            if body["id"] in self.docs:
                raise exceptions.CosmosResourceExistsError(status_code=409, message="exists")
            self.docs[body["id"]] = {**body, "_etag": "1"}

    def replace_item(self, item, body, etag=None, match_condition=None):
        if self.before_replace:
            self.before_replace()
        with self.lock:
            current = self.docs[item]
            if etag != current["_etag"]:
                self.conflicts += 1
                raise exceptions.CosmosAccessConditionFailedError(status_code=412, message="etag")
            self.docs[item] = {**body, "_etag": str(int(current["_etag"]) + 1)}


def _ist(y, m, d, hh, mm):
    """Unix seconds for an IST wall-clock time."""
    return int(datetime(y, m, d, hh, mm, tzinfo=tax_invoices.IST).astimezone(timezone.utc).timestamp())


# ── Tax split ─────────────────────────────────────────────────────────────────
class TestSplitTax:
    def test_intra_state_starter_monthly(self):
        parts = tax_invoices.split_tax(Decimal("707"), intra_state=True)
        assert parts["taxable_value"] == Decimal("599.15")
        assert parts["cgst"] == Decimal("53.92")
        assert parts["sgst"] == Decimal("53.93")  # the odd paisa goes to SGST
        assert parts["igst"] == Decimal("0.00")

    def test_inter_state_is_igst(self):
        parts = tax_invoices.split_tax(Decimal("1769"), intra_state=False)
        assert parts["taxable_value"] == Decimal("1499.15")
        assert parts["igst"] == Decimal("269.85")
        assert parts["cgst"] == parts["sgst"] == Decimal("0.00")

    @pytest.mark.parametrize("total", ["707", "7079", "1769", "1", "0.99", "123456.78", "99999.99"])
    @pytest.mark.parametrize("intra", [True, False])
    def test_parts_always_add_up_to_the_charge(self, total, intra):
        parts = tax_invoices.split_tax(Decimal(total), intra_state=intra)
        assert parts["taxable_value"] + parts["cgst"] + parts["sgst"] + parts["igst"] == Decimal(total)


class TestAmountInWords:
    @pytest.mark.parametrize("amount,words", [
        ("707", "Rupees Seven Hundred Seven Only"),
        ("7079", "Rupees Seven Thousand Seventy Nine Only"),
        ("1769.18", "Rupees One Thousand Seven Hundred Sixty Nine and Eighteen Paise Only"),
        ("250000", "Rupees Two Lakh Fifty Thousand Only"),
        ("12500000.05", "Rupees One Crore Twenty Five Lakh and Five Paise Only"),
    ])
    def test_indian_numbering(self, amount, words):
        assert tax_invoices.amount_in_words(Decimal(amount)) == words


# ── Financial year and numbering ─────────────────────────────────────────────
class TestNumbering:
    def test_financial_year(self):
        assert tax_invoices.financial_year(date(2026, 10, 11)) == "2026-27"
        assert tax_invoices.financial_year(date(2027, 3, 31)) == "2026-27"
        assert tax_invoices.financial_year(date(2027, 4, 1)) == "2027-28"
        assert tax_invoices.financial_year(date(2099, 4, 1)) == "2099-00"

    def test_ist_date_uses_india_time(self):
        # 31 Mar 2027 23:59 IST is still 31 Mar; 1 Apr 00:00 IST is 31 Mar 18:30 UTC but already April in India.
        assert tax_invoices.ist_date(_ist(2027, 3, 31, 23, 59)) == date(2027, 3, 31)
        assert tax_invoices.ist_date(_ist(2027, 4, 1, 0, 0)) == date(2027, 4, 1)

    def test_numbers_run_per_year_and_restart_on_1_april(self, monkeypatch):
        counters = FakeCounters()
        monkeypatch.setattr(tax_invoices, "counters_container", counters)
        march = tax_invoices.financial_year(tax_invoices.ist_date(_ist(2027, 3, 31, 23, 59)))
        april = tax_invoices.financial_year(tax_invoices.ist_date(_ist(2027, 4, 1, 0, 0)))
        assert [tax_invoices.format_number(march, tax_invoices.next_number(march)) for _ in range(2)] == [
            "SB/2026-27/0001", "SB/2026-27/0002"]
        assert tax_invoices.format_number(april, tax_invoices.next_number(april)) == "SB/2027-28/0001"
        assert tax_invoices.format_number(march, tax_invoices.next_number(march)) == "SB/2026-27/0003"

    def test_two_concurrent_calls_never_share_a_number(self, monkeypatch):
        counters = FakeCounters()
        counters.docs["tax_invoice:2026-27"] = {"id": "tax_invoice:2026-27", "value": 6, "_etag": "1"}
        barrier = threading.Barrier(2)
        first_round = threading.local()

        def wait_for_other():
            # Both callers read the same ETag before either replaces, forcing one conflict.
            if not getattr(first_round, "done", False):
                first_round.done = True
                barrier.wait(timeout=5)

        counters.before_replace = wait_for_other
        monkeypatch.setattr(tax_invoices, "counters_container", counters)
        results = []
        threads = [threading.Thread(target=lambda: results.append(tax_invoices.next_number("2026-27")))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert sorted(results) == [7, 8]
        assert counters.conflicts == 1

    def test_counter_that_stays_busy_raises(self, monkeypatch):
        counters = FakeCounters()
        counters.docs["tax_invoice:2026-27"] = {"id": "tax_invoice:2026-27", "value": 1, "_etag": "1"}
        bumps = iter(range(100, 200))
        # Someone else writes between every read and replace.
        counters.before_replace = lambda: counters.docs["tax_invoice:2026-27"].update(_etag=str(next(bumps)))
        monkeypatch.setattr(tax_invoices, "counters_container", counters)
        with pytest.raises(tax_invoices.CounterBusyError):
            tax_invoices.next_number("2026-27")

    def test_series_prefix_from_config(self, monkeypatch):
        monkeypatch.setenv("INVOICE_SERIES_PREFIX", "SBX")
        assert tax_invoices.format_number("2026-27", 12) == "SBX/2026-27/0012"


# ── Buyer ─────────────────────────────────────────────────────────────────────
class TestBuyer:
    def test_state_from_gstin_first(self):
        profile = {"organization_name": "Acme Traders", "gstin": "27AAAAA0000A1Z5",
                   "address": {"line1": "1 Road", "city": "Pune", "state": "Punjab", "pincode": "411001"}}
        buyer = tax_invoices.buyer_details({"name": "Acme"}, profile)
        assert buyer["state_code"] == "27" and buyer["state"] == "Maharashtra"
        assert buyer["registration"] == "Registered"
        assert buyer["address_lines"] == ["1 Road", "Pune", "Punjab 411001"]

    def test_unregistered_buyer_uses_address_state(self):
        buyer = tax_invoices.buyer_details({"name": "Acme"}, {"gstin": "", "address": {"state": "Karnataka"}})
        assert buyer["gstin"] == "" and buyer["registration"] == "Unregistered"
        assert buyer["state_code"] == "29"

    def test_invalid_gstin_is_ignored(self):
        buyer = tax_invoices.buyer_details({"name": "Acme"}, {"gstin": "NOT-A-GSTIN", "address": {}})
        assert buyer["gstin"] == "" and buyer["state_code"] == ""
        assert buyer["name"] == "Acme"


# ── Webhook creates the invoice ──────────────────────────────────────────────
PAYMENT_TIME = _ist(2026, 10, 11, 10, 0)


def _charged(amount=70700, payment_id="pay_1", status="captured", plan_id="plan_starter_m"):
    return {
        "entity": "event",
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {
                "id": "sub_1", "plan_id": plan_id, "customer_id": "cust_1", "status": "active",
                "current_start": _ist(2026, 10, 11, 0, 0), "current_end": _ist(2026, 11, 11, 0, 0),
                "notes": {"tenant_id": TENANT_A},
            }},
            "payment": {"entity": {"id": payment_id, "amount": amount, "currency": "INR",
                                   "status": status, "created_at": PAYMENT_TIME}},
        },
    }


@pytest.fixture()
def store(app, monkeypatch):
    """Billing mocks with a fresh counter and an empty tax_invoices container."""
    from smart_invoice_pro.api import billing_api, roles_api
    roles_api.users_container.query_items.return_value = [{"id": "u", "tenant_id": TENANT_A, "role": "Admin"}]
    counters = FakeCounters()
    monkeypatch.setattr(tax_invoices, "counters_container", counters)
    tax_invoices.tax_invoices_container.read_item.side_effect = exceptions.CosmosResourceNotFoundError(
        status_code=404, message="missing")
    return billing_api, counters


def _profile(**fields):
    base = {"id": f"{TENANT_A}:organization_profile", "tenant_id": TENANT_A, "organization_name": "Acme Traders",
            "gstin": "03BBBBB1111B1Z5", "address": {"line1": "2 Lane", "city": "Ludhiana", "state": "Punjab"}}
    base.update(fields)
    return base


def _created_invoice():
    return tax_invoices.tax_invoices_container.create_item.call_args.kwargs["body"]


class TestWebhookInvoice:
    def _send(self, client, billing_api, payload, profile=None, event_id="evt_1"):
        billing_api.settings_container.query_items.return_value = [profile] if profile else []
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id",
                   return_value=_tenant(billing={"status": "active", "subscription_id": "sub_1"})):
            return _send(client, payload, event_id=event_id)

    def test_intra_state_invoice(self, client, store, billing_env):
        billing_api, _ = store
        resp = self._send(client, billing_api, _charged(), _profile())
        assert resp.status_code == 200
        inv = _created_invoice()
        assert inv["id"] == "tinv_pay_1" and inv["tenant_id"] == TENANT_A
        assert inv["number"] == "SB/2026-27/0001"
        assert inv["issue_date"] == "2026-10-11"
        assert inv["supply_type"] == "intra-state"
        assert (inv["taxable_value"], inv["cgst"], inv["sgst"], inv["igst"], inv["total"]) == (
            599.15, 53.92, 53.93, 0.0, 707.0)
        assert inv["seller"]["gstin"] == "03AAAAA0000A1Z5"
        assert inv["seller"]["address_lines"] == ["1 Test Street", "Mohali, Punjab 140000"]
        assert inv["buyer"]["gstin"] == "03BBBBB1111B1Z5"
        assert inv["sac"] == "997331"
        assert inv["payment_ref"] == "pay_1" and inv["subscription_id"] == "sub_1"
        assert inv["description"] == "Solidev Books Starter monthly subscription, 11 Oct 2026 – 11 Nov 2026"
        assert inv["amount_in_words"] == "Rupees Seven Hundred Seven Only"

    def test_inter_state_invoice(self, client, store, billing_env):
        billing_api, _ = store
        self._send(client, billing_api, _charged(amount=176900, plan_id="plan_growth_m"),
                   _profile(gstin="29BBBBB1111B1Z5"))
        inv = _created_invoice()
        assert inv["supply_type"] == "inter-state"
        assert inv["place_of_supply"] == {"state_code": "29", "state": "Karnataka"}
        assert (inv["igst"], inv["cgst"], inv["sgst"]) == (269.85, 0.0, 0.0)
        assert inv["plan_code"] == "growth"

    def test_unregistered_buyer_without_profile(self, client, store, billing_env):
        billing_api, _ = store
        self._send(client, billing_api, _charged(), profile=None)
        inv = _created_invoice()
        assert inv["buyer"]["registration"] == "Unregistered"
        assert inv["buyer"]["name"] == "Acme"
        # No known buyer state: place of supply is the supplier's location.
        assert inv["supply_type"] == "intra-state" and inv["place_of_supply"]["state_code"] == "03"

    def test_same_payment_twice_gets_one_invoice(self, client, store, billing_env):
        billing_api, counters = store
        tax_invoices.tax_invoices_container.read_item.side_effect = None
        tax_invoices.tax_invoices_container.read_item.return_value = {"id": "tinv_pay_1"}
        self._send(client, billing_api, _charged(), _profile(), event_id="evt_2")
        tax_invoices.tax_invoices_container.create_item.assert_not_called()
        assert counters.docs == {}

    def test_payment_not_captured_gets_no_invoice(self, client, store, billing_env):
        billing_api, _ = store
        self._send(client, billing_api, _charged(status="failed"), _profile())
        tax_invoices.tax_invoices_container.create_item.assert_not_called()

    def test_missing_seller_details_still_applies_the_plan(self, client, store, billing_env, monkeypatch):
        billing_api, _ = store
        monkeypatch.delenv("BILLING_SAC_CODE")
        resp = self._send(client, billing_api, _charged(), _profile())
        assert resp.status_code == 200
        tax_invoices.tax_invoices_container.create_item.assert_not_called()
        assert billing_api.tenants_container.replace_item.call_args.kwargs["body"]["plan"] == "starter"

    def test_busy_counter_releases_event_for_retry(self, client, store, billing_env, monkeypatch):
        billing_api, _ = store
        monkeypatch.setattr(tax_invoices, "next_number", lambda fy: (_ for _ in ()).throw(
            tax_invoices.CounterBusyError(fy)))
        resp = self._send(client, billing_api, _charged(), _profile(), event_id="evt_7")
        assert resp.status_code == 500
        billing_api.billing_events_container.delete_item.assert_called_once_with(item="evt_7", partition_key="evt_7")


# ── List and PDF ──────────────────────────────────────────────────────────────
def _stored_invoice(tenant_id=TENANT_A, intra=True):
    return {
        "id": "tinv_pay_1", "tenant_id": tenant_id, "number": "SB/2026-27/0001", "issue_date": "2026-10-11",
        "seller": {"legal_name": "Example Seller Private Limited", "cin": "U00000PB2000PTC000000",
                   "gstin": "03AAAAA0000A1Z5", "address_lines": ["1 Test Street"], "state": "Punjab",
                   "state_code": "03", "email": ""},
        "buyer": {"name": "Acme Traders", "gstin": "", "address_lines": [], "state": "", "state_code": ""},
        "place_of_supply": {"state_code": "03", "state": "Punjab"},
        "supply_type": "intra-state" if intra else "inter-state", "sac": "997331",
        "description": "Solidev Books Starter monthly subscription, 11 Oct 2026 – 11 Nov 2026",
        "taxable_value": 599.15, "cgst": 53.92, "sgst": 53.93, "igst": 0.0, "total_tax": 107.85, "total": 707.0,
        "cgst_rate": 9, "sgst_rate": 9, "igst_rate": 0,
        "amount_in_words": "Rupees Seven Hundred Seven Only", "payment_ref": "pay_1",
    }


class TestInvoiceEndpoints:
    def test_list(self, client, store):
        billing_api, _ = store
        tax_invoices.tax_invoices_container.query_items.return_value = [{"id": "tinv_pay_1"}]
        resp = client.get("/api/billing/invoices", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.get_json()["invoices"] == [{"id": "tinv_pay_1"}]
        assert tax_invoices.tax_invoices_container.query_items.call_args.kwargs["partition_key"] == TENANT_A

    @pytest.mark.parametrize("intra", [True, False])
    def test_pdf_download(self, client, store, intra):
        billing_api, _ = store
        billing_api.tax_invoices_container.read_item.return_value = _stored_invoice(intra=intra)
        resp = client.get("/api/billing/invoices/tinv_pay_1/pdf", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")
        assert resp.headers["Content-Disposition"] == 'attachment; filename="SB-2026-27-0001.pdf"'
        assert billing_api.tax_invoices_container.read_item.call_args.kwargs["partition_key"] == TENANT_A

    def test_other_tenants_invoice_is_404(self, client, store):
        billing_api, _ = store
        billing_api.tax_invoices_container.read_item.side_effect = exceptions.CosmosResourceNotFoundError(
            status_code=404, message="missing")
        resp = client.get("/api/billing/invoices/tinv_pay_1/pdf", headers=auth_headers(tenant_id=TENANT_B))
        assert resp.status_code == 404

    def test_mismatched_tenant_field_is_404(self, client, store):
        billing_api, _ = store
        billing_api.tax_invoices_container.read_item.return_value = _stored_invoice(tenant_id=TENANT_B)
        assert client.get("/api/billing/invoices/tinv_pay_1/pdf", headers=auth_headers()).status_code == 404

    def test_only_admin(self, client, store):
        from smart_invoice_pro.api import roles_api
        roles_api.users_container.query_items.return_value = [{"id": "u", "role": "Sales"}]
        assert client.get("/api/billing/invoices", headers=auth_headers()).status_code == 403
        assert client.get("/api/billing/invoices/tinv_pay_1/pdf", headers=auth_headers()).status_code == 403

    def test_needs_sign_in(self, client, store):
        assert client.get("/api/billing/invoices").status_code == 401

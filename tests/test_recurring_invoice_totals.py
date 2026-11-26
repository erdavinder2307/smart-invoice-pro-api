"""
Recurring invoices must get the same totals as a normal invoice made from the
same items (SE-4060 follow-up).

There are two recurring-invoice generators:
  * POST /api/cron/generate-recurring  (smart_invoice_pro/api/cron_jobs.py)
  * process_recurring_profiles()       (smart_invoice_pro/services/scheduler.py)

Each test runs against both generators and compares with the invoice update
endpoint (PUT /api/invoices/<id>), which totals the lines with
_compute_item_totals() and _resolve_total_tax(). All amounts are made up.
"""
import copy
from unittest.mock import patch

import pytest

from tests.conftest import TENANT_A

NEXT_RUN_PATCH = "smart_invoice_pro.api.recurring_profiles_api.calculate_next_run_date"
INVOICE_NUMBER_PATCH = "smart_invoice_pro.api.invoice_preferences_api.generate_invoice_number"


def _profile(items, cgst=0.0, sgst=0.0, igst=0.0, is_gst_applicable=True):
    return {
        "id": "prof-totals-001",
        "tenant_id": TENANT_A,
        "customer_id": "cust-001",
        "customer_name": "Acme Corp",
        "profile_name": "Monthly Retainer",
        "frequency": "Monthly",
        "recurrence_rule": {},
        "next_run_date": "2024-01-01",
        "last_run_date": None,
        "status": "Active",
        "auto_send": False,
        "occurrences_created": 0,
        "occurrence_limit": None,
        "ends_type": "never",
        "end_date": None,
        "items": items,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "igst_amount": igst,
        "is_gst_applicable": is_gst_applicable,
        "payment_terms": "Net 30",
        "notes": "",
        "terms_conditions": "",
    }


def _line(**fields):
    return {"name": "Consulting", "quantity": 1, "rate": 1000, "discount": 0, "tax": 0, **fields}


def _generate_via_cron(client, cron_headers, profile):
    with patch("smart_invoice_pro.api.cron_jobs.create_notification"), \
         patch("smart_invoice_pro.api.cron_jobs.log_audit"), \
         patch("smart_invoice_pro.api.cron_jobs.invoices_container") as mock_inv, \
         patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container") as mock_rp, \
         patch(NEXT_RUN_PATCH, return_value="2024-02-01"), \
         patch(INVOICE_NUMBER_PATCH, return_value="INV-00042"):
        mock_rp.query_items.return_value = [copy.deepcopy(profile)]
        resp = client.post("/api/cron/generate-recurring", headers=cron_headers)
    assert resp.status_code == 200
    assert resp.get_json()["generated_count"] == 1
    return mock_inv.create_item.call_args.kwargs["body"]


def _generate_via_scheduler(client, cron_headers, profile):
    from smart_invoice_pro.services.scheduler import process_recurring_profiles

    with patch("smart_invoice_pro.utils.cosmos_client.invoices_container") as mock_inv, \
         patch("smart_invoice_pro.utils.cosmos_client.recurring_profiles_container") as mock_rp, \
         patch(NEXT_RUN_PATCH, return_value="2024-02-01"):
        mock_rp.query_items.return_value = [copy.deepcopy(profile)]
        mock_inv.query_items.return_value = []  # no earlier invoice -> first invoice number
        process_recurring_profiles()
        # The scheduler logs and swallows errors, so make sure it really saved one.
        assert mock_inv.create_item.call_count == 1
        return mock_inv.create_item.call_args.kwargs["body"]


@pytest.fixture(params=["cron", "scheduler"])
def generate(request, client, cron_headers):
    """Generate one invoice from a recurring profile; returns the saved invoice."""
    runner = _generate_via_cron if request.param == "cron" else _generate_via_scheduler
    return lambda profile: runner(client, cron_headers, profile)


@pytest.fixture()
def normal_invoice(client, headers_a, stored_invoice_a):
    """Totals a normal invoice gets for the same items and stored CGST/SGST/IGST."""
    def _totals(items, cgst=0.0, sgst=0.0, igst=0.0):
        stored = copy.deepcopy(stored_invoice_a)
        stored.update({
            "status": "Draft",
            "is_gst_applicable": True,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
            "items": copy.deepcopy(items),
        })
        with patch("smart_invoice_pro.api.invoices.must_suppress_sales_tax", return_value=False), \
             patch("smart_invoice_pro.api.invoices.invoices_container") as mock_inv:
            mock_inv.query_items.return_value = [stored]
            resp = client.put("/api/invoices/inv-aaa-001", json={"notes": "edited"}, headers=headers_a)
            assert resp.status_code == 200
            return mock_inv.replace_item.call_args.kwargs["body"]
    return _totals


class TestRecurringLineTax:
    """(a) A line tax rate is charged even when the profile's stored split is 0."""

    def test_line_tax_with_zero_stored_split(self, generate, normal_invoice):
        items = [_line(quantity=1, rate=1000, tax=18)]

        invoice = generate(_profile(items))

        assert invoice["subtotal"] == pytest.approx(1000.0)
        assert invoice["total_tax"] == pytest.approx(180.0)
        assert invoice["total_amount"] == pytest.approx(1180.0)
        assert invoice["balance_due"] == pytest.approx(1180.0)

        normal = normal_invoice(items)
        assert normal["total_tax"] == pytest.approx(invoice["total_tax"])
        assert normal["total_amount"] == pytest.approx(invoice["total_amount"])

    def test_stored_split_is_not_added_on_top_of_line_tax(self, generate):
        """SE-4060: the line tax already holds the full GST."""
        items = [_line(quantity=1, rate=1000, tax=18)]

        invoice = generate(_profile(items, cgst=90.0, sgst=90.0))

        assert invoice["total_tax"] == pytest.approx(180.0)
        assert invoice["total_amount"] == pytest.approx(1180.0)


class TestRecurringLineDiscount:
    """(b) A line discount is applied, and tax is charged on the discounted base."""

    def test_discount_matches_normal_invoice(self, generate, normal_invoice):
        # 4 x 250 = 1000, minus a flat 100 -> 900; 18% of 900 = 162
        items = [
            _line(name="Plan", quantity=4, rate=250, discount=100, tax=18),
            _line(name="Support", quantity=2, rate=100, discount=0, tax=5),
        ]

        invoice = generate(_profile(items))

        assert invoice["subtotal"] == pytest.approx(1100.0)
        assert invoice["total_tax"] == pytest.approx(172.0)
        assert invoice["total_amount"] == pytest.approx(1272.0)

        normal = normal_invoice(items)
        assert normal["subtotal"] == pytest.approx(invoice["subtotal"])
        assert normal["total_tax"] == pytest.approx(invoice["total_tax"])
        assert normal["total_amount"] == pytest.approx(invoice["total_amount"])


class TestRecurringManualSplit:
    """(c) With no line tax rate, the profile's CGST/SGST split is kept."""

    def test_manual_split_kept_when_lines_carry_no_tax(self, generate, normal_invoice):
        items = [_line(quantity=1, rate=1000, tax=0)]

        invoice = generate(_profile(items, cgst=90.0, sgst=90.0))

        assert invoice["cgst_amount"] == pytest.approx(90.0)
        assert invoice["sgst_amount"] == pytest.approx(90.0)
        assert invoice["igst_amount"] == pytest.approx(0.0)
        assert invoice["total_tax"] == pytest.approx(180.0)
        assert invoice["total_amount"] == pytest.approx(1180.0)

        normal = normal_invoice(items, cgst=90.0, sgst=90.0)
        assert normal["total_tax"] == pytest.approx(invoice["total_tax"])
        assert normal["total_amount"] == pytest.approx(invoice["total_amount"])


class TestRecurringGstNotApplicable:
    """A profile that is not GST-applicable gets no tax, like a normal invoice."""

    def test_no_tax_when_gst_not_applicable(self, generate):
        items = [_line(quantity=1, rate=1000, tax=18)]

        invoice = generate(_profile(items, cgst=90.0, sgst=90.0, is_gst_applicable=False))

        assert invoice["total_tax"] == pytest.approx(0.0)
        assert invoice["total_amount"] == pytest.approx(1000.0)
        assert invoice["cgst_amount"] == pytest.approx(0.0)
        assert invoice["sgst_amount"] == pytest.approx(0.0)

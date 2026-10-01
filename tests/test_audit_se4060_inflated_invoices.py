"""
Tests for scripts/audit_se4060_inflated_invoices.py, the read-only SE-4060 audit.
No database: invoices are plain dicts and lookups are stubs.
"""
import importlib.util
import pathlib
import re
from datetime import datetime
from unittest.mock import MagicMock

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit_se4060_inflated_invoices.py"

_spec = importlib.util.spec_from_file_location("audit_se4060", SCRIPT)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _invoice(**fields):
    base = {
        "id": "inv-1",
        "invoice_number": "INV-0001",
        "tenant_id": "tenant-1",
        "customer_id": "cust-1",
        "customer_name": "Acme",
        "issue_date": "2026-09-10",
        "created_at": "2026-09-10T06:00:00",
        "status": "Issued",
        "is_gst_applicable": True,
        "items": [{"quantity": 1, "rate": 1000, "discount": 0, "tax": 18}],
        "cgst_amount": 90.0,
        "sgst_amount": 90.0,
        "igst_amount": 0.0,
        "total_tax": 180.0,
        "total_amount": 1180.0,
        "amount_paid": 0.0,
        "balance_due": 1180.0,
        "place_of_supply": "Punjab",
    }
    base.update(fields)
    return base


class TestNoWrites:
    def test_source_has_no_write_calls(self):
        source = SCRIPT.read_text()
        for call in ("create_item", "replace_item", "upsert_item", "delete_item", "patch_item"):
            assert not re.search(rf"\b{call}\b", source), f"{call} found in audit script"


class TestInflatedReport:
    def test_inflated_invoice_is_reported_with_difference(self):
        inv = _invoice(total_tax=360.0, total_amount=1360.0, balance_due=1360.0)
        rows = audit.find_inflated([inv])
        assert len(rows) == 1
        row = rows[0]
        assert row["stored_total_tax"] == 360.0
        assert row["expected_total_tax"] == 180.0
        assert row["stored_total_amount"] == 1360.0
        assert row["expected_total_amount"] == 1180.0
        assert row["difference"] == 180.0
        assert row["reason"] == "line tax + stored split"

    def test_correct_invoice_is_not_reported(self):
        assert audit.find_inflated([_invoice()]) == []

    def test_manual_gst_only_invoice_is_not_reported(self):
        inv = _invoice(items=[{"quantity": 1, "rate": 1000, "tax": 0}])
        assert audit.find_inflated([inv]) == []

    def test_gst_off_invoice_is_not_reported(self):
        inv = _invoice(is_gst_applicable=False, total_tax=360.0, total_amount=1360.0)
        assert audit.find_inflated([inv]) == []

    def test_row_has_no_contact_details(self):
        inv = _invoice(total_tax=360.0, total_amount=1360.0,
                       customer_email="a@example.com", customer_phone="99999")
        row = audit.find_inflated([inv])[0]
        assert set(row) == set(audit.FIELDS)
        assert "a@example.com" not in row.values() and "99999" not in row.values()


def _zero_gst(**fields):
    return _invoice(**{"gst_treatment": "consumer", "cgst_amount": 0.0, "sgst_amount": 0.0,
                       "total_tax": 0.0, "total_amount": 1000.0, "balance_due": 1000.0, **fields})


def _run_zero_gst(invoices, suppress=False):
    return audit.find_zero_gst(
        invoices,
        audit.PR60_CUTOFF_UTC,
        seller_suppresses_tax=lambda tenant: suppress,
        seller_state=lambda tenant: "Punjab",
        customer_info=MagicMock(return_value=("Punjab", "regular", "Punjab")),
    )


class TestZeroGstReport:
    def test_consumer_invoice_before_cutoff_is_reported(self):
        rows = _run_zero_gst([_zero_gst()])
        assert len(rows) == 1
        row = rows[0]
        assert row["expected_total_tax"] == 180.0
        assert row["expected_total_amount"] == 1180.0
        assert row["difference"] == -180.0
        assert row["gst_treatment"] == "consumer"
        assert row["reason"] == "zero GST, pre-PR #60 treatment"

    def test_composition_customer_before_cutoff_is_reported(self):
        assert len(_run_zero_gst([_zero_gst(gst_treatment="composition")])) == 1

    def test_invoice_after_cutoff_is_not_reported(self):
        assert _run_zero_gst([_zero_gst(created_at="2026-09-23T09:00:00", issue_date="2026-09-23")]) == []

    def test_issue_date_is_used_when_created_at_missing(self):
        assert len(_run_zero_gst([_zero_gst(created_at=None, issue_date="2026-09-22")])) == 1
        assert _run_zero_gst([_zero_gst(created_at=None, issue_date="2026-09-24")]) == []

    def test_sez_invoice_is_not_reported(self):
        assert _run_zero_gst([_zero_gst(gst_treatment="special_economic_zone")]) == []

    def test_composition_scheme_seller_is_not_reported(self):
        assert _run_zero_gst([_zero_gst()], suppress=True) == []

    def test_customer_treatment_used_when_invoice_has_none(self):
        rows = audit.find_zero_gst(
            [_zero_gst(gst_treatment="", place_of_supply="")],
            audit.PR60_CUTOFF_UTC,
            seller_suppresses_tax=lambda tenant: False,
            seller_state=lambda tenant: "Punjab",
            customer_info=lambda tenant, cid: ("Haryana", "consumer", ""),
        )
        assert len(rows) == 1 and rows[0]["expected_total_tax"] == 180.0


class TestHelpers:
    def test_before_date_is_midnight_ist(self):
        assert audit._parse_cutoff("2026-09-23") == datetime(2026, 9, 22, 18, 30)

    def test_query_filters_and_limit(self):
        container = MagicMock()
        container.query_items.return_value = iter([{"id": str(i)} for i in range(5)])
        result = audit.query_invoices(container, since="2026-09-01", tenant="t1", limit=2)
        assert [r["id"] for r in result] == ["0", "1"]
        kwargs = container.query_items.call_args.kwargs
        assert "c.issue_date >= @since" in kwargs["query"] and "c.tenant_id = @tid" in kwargs["query"]

    @pytest.mark.parametrize("report,expected", [("se4060", "x.csv"), ("both", "x-se4060.csv")])
    def test_out_path(self, report, expected):
        assert audit._out_path("x.csv", "se4060", report == "both") == expected

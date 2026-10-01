"""
Tests for scripts/correct_se4062_invoices.py, the SE-4062 correction script.
No database: invoices are plain dicts and the fetch/save/log callables are stubs.
"""
import importlib.util
import io
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "correct_se4062_invoices.py"

_spec = importlib.util.spec_from_file_location("correct_se4062", SCRIPT)
fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix)


def _invoice(**fields):
    # Inflated by SE-4060: line tax 180 + stored split 180 saved as total_tax 360.
    base = {
        "id": "inv-1",
        "invoice_number": "INV-0001",
        "tenant_id": "tenant-1",
        "customer_id": "cust-1",
        "status": "Issued",
        "is_gst_applicable": True,
        "items": [{"quantity": 1, "rate": 1000, "discount": 0, "tax": 18}],
        "cgst_amount": 90.0,
        "sgst_amount": 90.0,
        "igst_amount": 0.0,
        "total_tax": 360.0,
        "total_amount": 1360.0,
        "amount_paid": 0.0,
        "balance_due": 1360.0,
        "_etag": "etag-1",
    }
    base.update(fields)
    return base


def _run(invoices, apply=False):
    by_id = {inv["id"]: inv for inv in invoices}
    saved, logged = [], []
    out = io.StringIO()
    counts = fix.correct(
        list(by_id) + (["missing"] if apply == "with-missing" else []),
        by_id.get,
        saved.append,
        lambda before, after: logged.append((before, after)),
        lambda tenant_id: "standard",
        apply=bool(apply),
        out=out,
    )
    return counts, saved, logged, out.getvalue()


class TestPlan:
    def test_inflated_unpaid_invoice_gets_line_tax_totals(self):
        new_values, refusal = fix.plan_correction(_invoice())
        assert refusal is None
        assert new_values == {"total_tax": 180.0, "total_amount": 1180.0, "balance_due": 1180.0}

    def test_keeps_invoice_discount_and_round_off_in_total(self):
        # Stored total already holds discount -50 and round-off +0.4; only the tax overstatement is removed.
        new_values, _ = fix.plan_correction(_invoice(total_amount=1310.4, balance_due=1310.4))
        assert new_values["total_amount"] == 1130.4
        assert new_values["balance_due"] == 1130.4


class TestDryRun:
    def test_dry_run_writes_nothing_and_prints_before_after(self):
        counts, saved, logged, out = _run([_invoice()])
        assert counts["would_correct"] == 1
        assert saved == [] and logged == []
        assert "DRY RUN" in out
        assert "total_tax 360.00 -> 180.00" in out
        assert "total_amount 1,360.00 -> 1,180.00" in out
        assert "balance_due 1,360.00 -> 1,180.00" in out


class TestApply:
    def test_apply_changes_only_the_three_total_fields(self):
        original = _invoice()
        counts, saved, logged, _ = _run([original], apply=True)
        assert counts["corrected"] == 1
        assert len(saved) == 1
        changed = {k for k in saved[0] if saved[0][k] != original[k]}
        assert changed == {"total_tax", "total_amount", "balance_due"}
        assert saved[0]["_etag"] == "etag-1"

    def test_apply_logs_before_and_after(self):
        _, _, logged, _ = _run([_invoice()], apply=True)
        before, after = logged[0]
        assert before["total_tax"] == 360.0
        assert after["total_tax"] == 180.0

    def test_missing_id_is_counted_not_written(self):
        counts, saved, _, out = _run([_invoice()], apply="with-missing")
        assert counts["not_found"] == 1
        assert counts["corrected"] == 1
        assert "missing: NOT FOUND" in out


class TestRefusals:
    def _assert_refused(self, inv, reason_fragment):
        counts, saved, logged, out = _run([inv], apply=True)
        assert counts["refused"] == 1
        assert saved == [] and logged == []
        assert reason_fragment in out

    def test_refuses_paid_invoice(self):
        self._assert_refused(_invoice(status="Paid", amount_paid=1360.0, balance_due=0.0), "status is Paid")

    def test_refuses_partially_paid_invoice(self):
        self._assert_refused(
            _invoice(status="Partially Paid", amount_paid=100.0, balance_due=1260.0), "status is Partially Paid"
        )

    def test_refuses_invoice_with_payment_recorded(self):
        self._assert_refused(_invoice(status="Overdue", amount_paid=100.0), "a payment is recorded")

    def test_refuses_invoice_already_corrected(self):
        self._assert_refused(
            _invoice(total_tax=180.0, total_amount=1180.0, balance_due=1180.0), "no longer matches"
        )

    def test_refuses_invoice_edited_since_audit(self):
        # Tax changed to a value that is neither line tax nor line tax + split.
        self._assert_refused(_invoice(total_tax=250.0, total_amount=1250.0), "no longer matches")

    def test_refuses_zero_gst_invoice(self):
        self._assert_refused(
            _invoice(cgst_amount=0.0, sgst_amount=0.0, total_tax=0.0, total_amount=1000.0,
                     items=[{"quantity": 1, "rate": 1000, "discount": 0, "tax": 0}]),
            "no longer matches",
        )


class TestCli:
    def test_ids_are_required(self):
        try:
            fix.main([])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("main() without --ids should exit")

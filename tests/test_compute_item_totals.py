"""
Unit tests for _compute_item_totals() in invoices.py.

It normalises the line items and produces the subtotal and line-level tax for
both invoice create and update. It does no rounding of its own: create hands
its lines to calculate_gst(), which rounds per line (see
test_calculate_gst.py); update stores its figures as they are.
"""
import pytest

from smart_invoice_pro.api.invoices import _compute_item_totals


def _line(**fields):
    return {"quantity": 1, "rate": 0, "discount": 0, "tax": 0, **fields}


class TestLineArithmetic:
    def test_single_line_with_tax(self):
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=2, rate=500, tax=18)]
        )
        assert subtotal == 1000.0
        assert item_tax == 180.0
        assert items[0]["amount"] == 1180.0

    def test_discount_is_a_flat_amount_per_line(self):
        # 4 x 250 = 1000, minus a flat 100 (not 100 per unit, not 100%)
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=4, rate=250, discount=100, tax=18)]
        )
        assert subtotal == 900.0
        assert item_tax == pytest.approx(162.0)
        assert items[0]["amount"] == pytest.approx(1062.0)

    def test_tax_is_charged_on_the_discounted_base(self):
        _, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=1, rate=1000, discount=200, tax=5)]
        )
        assert subtotal == 800.0
        assert item_tax == 40.0

    def test_multiple_lines_with_mixed_rates_are_summed(self):
        items, subtotal, item_tax = _compute_item_totals([
            _line(quantity=1, rate=1000, tax=18),
            _line(quantity=2, rate=100, tax=5),
            _line(quantity=3, rate=50, tax=0),
        ])
        assert subtotal == 1350.0
        assert item_tax == 190.0
        assert [i["amount"] for i in items] == [1180.0, 210.0, 150.0]

    def test_fractional_quantity(self):
        _, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=2.5, rate=40, tax=12)]
        )
        assert subtotal == 100.0
        assert item_tax == pytest.approx(12.0)

    def test_empty_items(self):
        assert _compute_item_totals([]) == ([], 0.0, 0.0)


class TestNoRounding:
    def test_line_tax_is_not_rounded_to_paise(self):
        # 999.99 @ 18% = 179.9982; calculate_gst rounds this later, this
        # function must not round it on its own.
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=1, rate=999.99, tax=18)]
        )
        assert subtotal == pytest.approx(999.99)
        assert item_tax == pytest.approx(179.9982)
        assert item_tax != round(item_tax, 2)
        assert items[0]["amount"] == pytest.approx(1179.9882)

    def test_sub_paise_amounts_accumulate_without_rounding(self):
        # 3 lines of 0.012 @ 18%: tax 0.00216 each, 0.00648 in total
        _, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=1, rate=0.012, tax=18) for _ in range(3)]
        )
        assert subtotal == pytest.approx(0.036)
        assert item_tax == pytest.approx(0.00648)


class TestClamping:
    def test_discount_larger_than_line_clamps_base_to_zero(self):
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=1, rate=100, discount=150, tax=18)]
        )
        assert subtotal == 0.0
        assert item_tax == 0.0
        assert items[0]["amount"] == 0.0

    def test_discount_equal_to_line_gives_zero(self):
        _, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=2, rate=50, discount=100, tax=18)]
        )
        assert subtotal == 0.0
        assert item_tax == 0.0

    def test_clamped_line_does_not_reduce_other_lines(self):
        _, subtotal, item_tax = _compute_item_totals([
            _line(quantity=1, rate=100, discount=500, tax=18),
            _line(quantity=1, rate=200, tax=18),
        ])
        assert subtotal == 200.0
        assert item_tax == 36.0

    @pytest.mark.parametrize("field", ["quantity", "rate", "discount", "tax"])
    def test_negative_values_are_clamped_to_zero(self, field):
        base = _line(quantity=2, rate=100, discount=0, tax=10)
        items, _, _ = _compute_item_totals([{**base, field: -5}])
        assert items[0][field] == 0.0

    def test_negative_discount_does_not_increase_the_base(self):
        _, subtotal, _ = _compute_item_totals(
            [_line(quantity=1, rate=100, discount=-50)]
        )
        assert subtotal == 100.0

    def test_negative_tax_rate_gives_no_tax(self):
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=1, rate=100, tax=-18)]
        )
        assert subtotal == 100.0
        assert item_tax == 0.0
        assert items[0]["amount"] == 100.0


class TestGstNotApplicable:
    def test_no_tax_when_gst_not_applicable(self):
        items, subtotal, item_tax = _compute_item_totals(
            [_line(quantity=2, rate=500, discount=100, tax=18)],
            is_gst_applicable=False,
        )
        assert subtotal == 900.0
        assert item_tax == 0.0
        assert items[0]["amount"] == 900.0

    def test_tax_rate_is_kept_on_the_line_when_gst_not_applicable(self):
        items, _, _ = _compute_item_totals(
            [_line(quantity=1, rate=100, tax=18)], is_gst_applicable=False
        )
        assert items[0]["tax"] == 18.0

    def test_gst_is_applicable_by_default(self):
        _, _, item_tax = _compute_item_totals([_line(quantity=1, rate=100, tax=18)])
        assert item_tax == 18.0


class TestInputTolerance:
    def test_numeric_strings_are_parsed(self):
        items, subtotal, item_tax = _compute_item_totals(
            [{"quantity": "3", "rate": "19.99", "discount": "0.97", "tax": "12"}]
        )
        assert subtotal == pytest.approx(59.0)
        assert item_tax == pytest.approx(7.08)
        assert items[0]["quantity"] == 3.0
        assert items[0]["rate"] == 19.99

    @pytest.mark.parametrize("bad", [None, "", "abc", [], {}])
    def test_unparseable_values_count_as_zero(self, bad):
        items, subtotal, item_tax = _compute_item_totals(
            [{"quantity": 2, "rate": 100, "discount": bad, "tax": bad}]
        )
        assert subtotal == 200.0
        assert item_tax == 0.0
        assert items[0]["discount"] == 0.0
        assert items[0]["tax"] == 0.0

    def test_missing_fields_count_as_zero(self):
        items, subtotal, item_tax = _compute_item_totals([{"name": "Blank line"}])
        assert subtotal == 0.0
        assert item_tax == 0.0
        assert items[0]["quantity"] == 0.0
        assert items[0]["rate"] == 0.0
        assert items[0]["amount"] == 0.0


class TestNormalisedOutput:
    def test_numeric_fields_become_floats(self):
        items, _, _ = _compute_item_totals([_line(quantity=2, rate=100, tax=18)])
        for field in ("quantity", "rate", "discount", "tax", "amount"):
            assert isinstance(items[0][field], float)

    def test_other_fields_are_preserved(self):
        items, _, _ = _compute_item_totals([
            _line(quantity=1, rate=100, product_id="p-1", name="Widget", hsn_sac="8471")
        ])
        assert items[0]["product_id"] == "p-1"
        assert items[0]["name"] == "Widget"
        assert items[0]["hsn_sac"] == "8471"

    def test_client_supplied_amount_is_replaced(self):
        items, _, _ = _compute_item_totals(
            [_line(quantity=1, rate=100, tax=18, amount=999999)]
        )
        assert items[0]["amount"] == 118.0

    def test_input_items_are_not_mutated(self):
        original = {"quantity": "2", "rate": "100", "tax": "18", "amount": 5}
        snapshot = dict(original)
        _compute_item_totals([original])
        assert original == snapshot

    def test_line_order_is_kept(self):
        items, _, _ = _compute_item_totals(
            [_line(name=n, quantity=1, rate=10) for n in ("a", "b", "c")]
        )
        assert [i["name"] for i in items] == ["a", "b", "c"]

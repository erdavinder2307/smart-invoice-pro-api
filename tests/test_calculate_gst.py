"""
Unit tests for the calculate_gst() engine in tax_rates_api.

Complements TestCalculateGst in test_tax_rates.py, which covers the basic
intra/inter-state split and the zero-rated shortcuts. These tests pin the
arithmetic: per-line rounding to paise, multi-line totals, the base-amount
clamp, state matching, and tolerance of missing or string-typed fields.
"""
from smart_invoice_pro.api.tax_rates_api import calculate_gst


def _gst(items, seller="Delhi", customer="Delhi", treatment="regular", **kwargs):
    return calculate_gst(items, seller, customer, treatment, True, **kwargs)


class TestPerLineRounding:
    def test_intra_state_halves_are_rounded_to_paise_per_line(self):
        # base 999.99 @ 18% -> 89.9991 each half, rounded to 90.00
        result = _gst([{"quantity": 1, "rate": 999.99, "tax": 18}])
        line = result["items_with_tax"][0]
        assert line["cgst"] == 90.0
        assert line["sgst"] == 90.0
        assert line["igst"] == 0.0
        assert result["total_tax"] == 180.0

    def test_inter_state_line_is_rounded_to_paise(self):
        # base 25.05 @ 18% -> 4.509 IGST, rounded to 4.51
        result = _gst([{"quantity": 1, "rate": 25.05, "tax": 18}], customer="Punjab")
        assert result["items_with_tax"][0]["igst"] == 4.51
        assert result["igst_amount"] == 4.51

    def test_rounding_happens_per_line_before_summing(self):
        # Each line: base 0.10 @ 12% = 0.012 -> 0.01. Three lines sum to 0.03,
        # whereas rounding once on the combined base (0.036) would give 0.04.
        items = [{"quantity": 1, "rate": 0.10, "tax": 12}] * 3
        result = _gst(items, customer="Punjab")
        assert [line["igst"] for line in result["items_with_tax"]] == [0.01, 0.01, 0.01]
        assert result["igst_amount"] == 0.03
        assert result["total_tax"] == 0.03

    def test_intra_state_halves_are_rounded_independently(self):
        # base 0.14 @ 18%: each half is 0.0126 -> 0.01, so total tax is 0.02.
        # Halving a rounded IGST figure (0.0252 -> 0.03) would give 0.015 each.
        result = _gst([{"quantity": 1, "rate": 0.14, "tax": 18}])
        line = result["items_with_tax"][0]
        assert (line["cgst"], line["sgst"]) == (0.01, 0.01)
        assert (result["cgst_amount"], result["sgst_amount"]) == (0.01, 0.01)
        assert result["total_tax"] == 0.02

    def test_totals_have_no_float_drift_across_many_lines(self):
        # 10 lines of 33.33 @ 18% inter-state: 5.9994 -> 6.00 each, 60.00 total.
        items = [{"quantity": 1, "rate": 33.33, "tax": 18}] * 10
        result = _gst(items, customer="Punjab")
        assert result["igst_amount"] == 60.0
        assert result["total_tax"] == 60.0


class TestMultiLineTotals:
    def test_mixed_tax_rates_sum_per_component(self):
        items = [
            {"quantity": 2, "rate": 500, "tax": 18},   # base 1000 -> 90 + 90
            {"quantity": 4, "rate": 250, "tax": 5},    # base 1000 -> 25 + 25
            {"quantity": 1, "rate": 300, "tax": 0},    # exempt line -> 0
        ]
        result = _gst(items)
        assert result["cgst_amount"] == 115.0
        assert result["sgst_amount"] == 115.0
        assert result["igst_amount"] == 0.0
        assert result["total_tax"] == 230.0

    def test_items_keep_their_fields_and_gain_tax_columns(self):
        items = [{"product_id": "p-1", "name": "Widget", "quantity": 1, "rate": 100, "tax": 12}]
        result = _gst(items, customer="Punjab")
        line = result["items_with_tax"][0]
        assert line["product_id"] == "p-1"
        assert line["name"] == "Widget"
        assert (line["cgst"], line["sgst"], line["igst"]) == (0.0, 0.0, 12.0)
        # Input list is not mutated.
        assert "igst" not in items[0]

    def test_empty_items_on_taxable_invoice(self):
        result = _gst([])
        assert result["items_with_tax"] == []
        assert result["total_tax"] == 0.0
        assert result["tax_type"] == "CGST_SGST"


class TestBaseAmount:
    def test_discount_larger_than_line_clamps_base_to_zero(self):
        result = _gst([{"quantity": 1, "rate": 100, "discount": 150, "tax": 18}])
        line = result["items_with_tax"][0]
        assert (line["cgst"], line["sgst"]) == (0.0, 0.0)
        assert result["total_tax"] == 0.0

    def test_discount_is_per_line_amount_not_per_unit(self):
        # base = 3 * 100 - 30 = 270, IGST 18% = 48.60
        result = _gst([{"quantity": 3, "rate": 100, "discount": 30, "tax": 18}], customer="Punjab")
        assert result["igst_amount"] == 48.6

    def test_fractional_quantity(self):
        # base = 2.5 * 40 = 100, IGST 5% = 5.00
        result = _gst([{"quantity": 2.5, "rate": 40, "tax": 5}], customer="Punjab")
        assert result["igst_amount"] == 5.0

    def test_missing_and_none_fields_count_as_zero(self):
        items = [
            {"quantity": None, "rate": 100, "tax": 18},
            {"quantity": 1, "rate": 100},                 # no tax key
            {"quantity": 1, "rate": 100, "tax": None, "discount": None},
        ]
        result = _gst(items)
        assert result["total_tax"] == 0.0

    def test_numeric_strings_are_accepted(self):
        result = _gst([{"quantity": "2", "rate": "50.50", "discount": "1", "tax": "18"}],
                      customer="Punjab")
        # base = 2 * 50.50 - 1 = 100, IGST 18% = 18.00
        assert result["igst_amount"] == 18.0


class TestStateMatching:
    def test_state_match_ignores_case_and_whitespace(self):
        result = _gst([{"quantity": 1, "rate": 100, "tax": 18}],
                      seller="  Delhi ", customer="DELHI")
        assert result["is_intra_state"] is True
        assert result["tax_type"] == "CGST_SGST"

    def test_place_of_supply_can_make_supply_intra_state(self):
        result = _gst([{"quantity": 1, "rate": 100, "tax": 18}],
                      customer="Punjab", place_of_supply="Delhi")
        assert result["is_intra_state"] is True
        assert result["cgst_amount"] == 9.0

    def test_unknown_seller_state_falls_back_to_igst(self):
        result = _gst([{"quantity": 1, "rate": 100, "tax": 18}], seller="")
        assert result["is_intra_state"] is False
        assert result["igst_amount"] == 18.0

    def test_unknown_customer_state_falls_back_to_igst(self):
        result = _gst([{"quantity": 1, "rate": 100, "tax": 18}], customer=None)
        assert result["is_intra_state"] is False
        assert result["igst_amount"] == 18.0


class TestZeroRatedShortCircuit:
    def test_export_and_deemed_export_charge_no_tax(self):
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        for treatment in ("export", "deemed_export"):
            result = _gst(items, customer="Punjab", treatment=treatment)
            assert result["tax_type"] == "NONE", treatment
            assert result["total_tax"] == 0.0, treatment
            assert result["is_intra_state"] is None, treatment

    def test_zero_rated_returns_items_unchanged(self):
        items = [{"quantity": 1, "rate": 1000, "tax": 18}]
        result = _gst(items, treatment="special_economic_zone")
        assert result["items_with_tax"] is items

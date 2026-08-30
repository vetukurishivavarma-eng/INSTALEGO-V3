"""Money parsing, tolerance comparison and reconciliation totals."""

from decimal import Decimal

from app.utils.numbers import (
    amounts_equal,
    normalize_amount,
    parse_amount,
    percentage_difference,
    sum_amounts,
)


class TestParsing:
    def test_plain_number(self):
        assert parse_amount("500000") == Decimal("500000")

    def test_indian_digit_grouping(self):
        assert parse_amount("5,00,000") == Decimal("500000")

    def test_western_grouping(self):
        assert parse_amount("500,000.50") == Decimal("500000.50")

    def test_currency_symbols_and_suffixes(self):
        assert parse_amount("Rs. 5,00,000/-") == Decimal("500000")
        assert parse_amount("INR 500000") == Decimal("500000")

    def test_scale_words(self):
        assert parse_amount("5 Lakh") == Decimal("500000")
        assert parse_amount("1.5 Crore") == Decimal("15000000")

    def test_accounting_negative(self):
        assert parse_amount("(1,200.00)") == Decimal("-1200.00")

    def test_word_form_restatement_in_parentheses(self):
        """How a sanction letter actually writes an amount.

        Found by the degradation sweep. This used to return None, because the
        "/-" was no longer at the end of the string and survived digit
        stripping as a bare "-". An amount that will not parse is skipped by
        the comparison rules silently, so a real loan-amount mismatch on a
        letter phrased this way was never reported.
        """
        assert parse_amount("Rs. 5,00,000/- (Rupees Five Lakh only)") == Decimal("500000")
        assert parse_amount("5,00,000/- (Rupees Five Lakh Only)") == Decimal("500000")
        assert parse_amount("Rs 5 Lakh (Rupees Five Lakh only)") == Decimal("500000")

    def test_stacked_end_markers(self):
        assert parse_amount("Rs. 5,00,000/- only") == Decimal("500000")

    def test_a_parenthetical_holding_digits_is_still_a_negative(self):
        """Stripping the word form must not swallow the accounting convention."""
        assert parse_amount("(1,200.00)") == Decimal("-1200.00")
        assert parse_amount("Rs. (1,200)") == Decimal("-1200")

    def test_a_digitless_aside_is_ignored_wherever_it_sits(self):
        assert parse_amount("Total (net of tax) 42,000") == Decimal("42000")

    def test_words_alone_are_still_not_an_amount(self):
        """Reading English number words is a separate job, and guessing at one
        is worse than declining to compare."""
        assert parse_amount("Rupees Five Lakh only") is None

    def test_unparseable_returns_none(self):
        assert parse_amount("not an amount") is None
        assert parse_amount("") is None
        assert parse_amount(None) is None
        assert parse_amount("1.2.3") is None


class TestNormalisation:
    def test_equivalent_forms_normalise_identically(self):
        assert normalize_amount("5,00,000") == normalize_amount("500000.00")

    def test_decimals_are_preserved(self):
        assert normalize_amount("1234.56") == "1234.56"


class TestComparison:
    def test_exact_match_across_formats(self):
        equal, _ = amounts_equal("Rs. 5,00,000", "500000")
        assert equal is True

    def test_material_mismatch(self):
        equal, reason = amounts_equal("500000", "550000")
        assert equal is False
        assert "50000" in reason

    def test_within_percentage_tolerance(self):
        equal, _ = amounts_equal("500000", "500400", tolerance_pct=0.1)
        assert equal is True

    def test_outside_percentage_tolerance(self):
        equal, _ = amounts_equal("500000", "530000", tolerance_pct=1)
        assert equal is False

    def test_unparseable_side_is_undetermined(self):
        equal, _ = amounts_equal("500000", "see annexure")
        assert equal is None


class TestTotals:
    def test_sum(self):
        assert sum_amounts(["1,000", "2,000.50", "Rs. 500"]) == Decimal("3500.50")

    def test_sum_refuses_to_silently_skip_unreadable_entries(self):
        assert sum_amounts(["1,000", "illegible"]) is None

    def test_percentage_difference_is_symmetric(self):
        # Divided by the larger side: neither document is the baseline, so the
        # answer must not depend on argument order.
        assert percentage_difference("100", "110") == percentage_difference("110", "100")
        assert round(percentage_difference("100", "110"), 4) == 9.0909

    def test_percentage_difference_needs_both_sides(self):
        assert percentage_difference("100", "bad") is None

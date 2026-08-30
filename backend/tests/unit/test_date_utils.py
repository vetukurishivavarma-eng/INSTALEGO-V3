"""Date parsing, ambiguity handling, expiry and ordering."""

from datetime import date

from app.utils.dates import age_on, in_order, is_expired, parse_date, same_date, to_iso


class TestParsing:
    def test_iso_input(self):
        assert parse_date("1998-04-12").iso == "1998-04-12"

    def test_day_first_is_the_default_convention(self):
        assert parse_date("25/04/1998").iso == "1998-04-25"

    def test_textual_months(self):
        assert parse_date("12 April 1998").iso == "1998-04-12"
        assert parse_date("12-Apr-1998").iso == "1998-04-12"

    def test_unparseable_returns_no_value_rather_than_a_guess(self):
        parsed = parse_date("sometime in 1998")
        assert parsed.value is None
        assert parsed.iso is None
        assert to_iso("sometime in 1998") is None

    def test_empty_is_handled(self):
        assert parse_date(None).value is None
        assert parse_date("   ").value is None


class TestAmbiguity:
    def test_both_readings_valid_is_flagged(self):
        assert parse_date("12/04/1998").ambiguous is True

    def test_day_over_twelve_is_unambiguous(self):
        assert parse_date("25/04/1998").ambiguous is False

    def test_iso_is_never_ambiguous(self):
        assert parse_date("1998-04-12").ambiguous is False

    def test_identical_day_and_month_is_not_ambiguous(self):
        assert parse_date("04/04/1998").ambiguous is False


class TestComparison:
    def test_matching_dates_across_formats(self):
        equal, _ = same_date("12/04/1998", "1998-04-12")
        assert equal is True

    def test_clear_mismatch(self):
        equal, reason = same_date("12/04/1998", "12/04/1997")
        assert equal is False
        assert "1997" in reason

    def test_ambiguous_transposition_defers_to_a_human(self):
        equal, reason = same_date("12/04/1998", "1998-12-04")
        assert equal is None
        assert "ambiguous" in reason

    def test_unparseable_side_is_review_not_mismatch(self):
        equal, _ = same_date("12/04/1998", "not a date")
        assert equal is None


class TestExpiryAndOrdering:
    def test_past_date_is_expired(self):
        expired, reason = is_expired("01/01/2020", as_of=date(2026, 8, 29))
        assert expired is True
        assert "2020-01-01" in reason

    def test_future_date_is_valid(self):
        expired, _ = is_expired("01/01/2030", as_of=date(2026, 8, 29))
        assert expired is False

    def test_unparseable_expiry_is_undetermined(self):
        expired, _ = is_expired("unknown")
        assert expired is None

    def test_ordering(self):
        assert in_order("01/01/2020", "01/01/2021")[0] is True
        assert in_order("01/01/2021", "01/01/2020")[0] is False
        assert in_order("nonsense", "01/01/2020")[0] is None

    def test_age(self):
        assert age_on("12/04/1998", as_of=date(2026, 8, 29)) == 28
        assert age_on("bad input") is None

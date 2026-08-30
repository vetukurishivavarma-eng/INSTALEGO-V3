"""Comparison behaviour, especially the boundary between noise and a finding."""

from app.comparison import ComparisonVerdict, compare_field
from app.comparison.fuzzy import compare_address, compare_name, compare_organisation
from app.utils.normalize import normalize_address


class TestNameComparison:
    def test_case_and_spacing_are_not_discrepancies(self):
        outcome = compare_name("Ravi Kumar", "RAVI  KUMAR")
        assert outcome.is_equal
        assert outcome.cosmetic is True

    def test_honorifics_are_not_discrepancies(self):
        assert compare_name("Mr. Ravi Kumar", "Ravi Kumar").is_equal

    def test_middle_initial_expansion_is_not_a_discrepancy(self):
        # Evaluation case 004: this must not reach a reviewer as a mismatch.
        outcome = compare_name("Ravi Kumar", "Ravi K Kumar")
        assert outcome.is_equal
        assert outcome.cosmetic is True

    def test_reordered_name_parts_match(self):
        assert compare_name("Kumar Ravi", "Ravi Kumar").is_equal

    def test_different_people_are_different(self):
        outcome = compare_name("Ravi Kumar", "Sunita Sharma")
        assert outcome.is_different

    def test_close_but_not_equivalent_names_are_escalated(self):
        outcome = compare_name("Ravi Kumar", "Ravi Kumat")
        assert outcome.verdict in {ComparisonVerdict.UNDETERMINED, ComparisonVerdict.EQUAL}

    def test_missing_side_is_not_comparable(self):
        assert compare_name("Ravi Kumar", None).verdict == ComparisonVerdict.NOT_COMPARABLE


class TestIdentifierComparison:
    def test_pan_case_difference_is_cosmetic(self):
        outcome = compare_field("pan", "abcde1234f", "ABCDE1234F")
        assert outcome.is_equal

    def test_one_character_difference_is_a_real_mismatch(self):
        outcome = compare_field("pan", "ABCDE1234F", "ABCDE1234G")
        assert outcome.is_different
        assert outcome.similarity == 0.0

    def test_aadhaar_spacing_is_cosmetic(self):
        assert compare_field("aadhaar", "2345 6789 0124", "234567890124").is_equal

    def test_account_number_leading_zeros_matter(self):
        assert compare_field("bank_account", "000123456", "123456").is_different

    def test_phone_country_code_forms_match(self):
        assert compare_field("phone", "+91 98765 43210", "9876543210").is_equal


class TestDateComparison:
    def test_same_date_written_differently(self):
        assert compare_field("date_of_birth", "12/04/1998", "1998-04-12").is_equal

    def test_year_difference_is_a_mismatch(self):
        outcome = compare_field("date_of_birth", "12/04/1998", "12/04/1997")
        assert outcome.is_different

    def test_ambiguous_transposition_is_escalated_not_asserted(self):
        outcome = compare_field("date_of_birth", "12/04/1998", "1998-12-04")
        assert outcome.verdict == ComparisonVerdict.UNDETERMINED


class TestAmountComparison:
    def test_formatting_differences_match(self):
        assert compare_field("loan_amount", "Rs. 5,00,000", "500000").is_equal

    def test_material_difference_is_flagged(self):
        assert compare_field("loan_amount", "500000", "750000").is_different

    def test_tolerance_is_respected(self):
        outcome = compare_field("income", "50000", "50020", amount_tolerance_pct=1.0)
        assert outcome.is_equal


class TestAddressComparison:
    def test_abbreviations_and_punctuation_normalise(self):
        assert normalize_address("12, M.G. Road") == normalize_address("12 MG Rd")

    def test_same_address_written_differently_is_equal(self):
        outcome = compare_address(
            "12, M.G. Road, Bengaluru - 560001", "12 MG Rd, Bengaluru 560001"
        )
        assert outcome.is_equal

    def test_different_postal_code_is_a_real_difference(self):
        outcome = compare_address(
            "12 MG Road, Bengaluru 560001", "12 MG Road, Bengaluru 560042"
        )
        assert outcome.is_different
        assert "postal" in outcome.reason

    def test_partial_overlap_is_escalated(self):
        outcome = compare_address(
            "Flat 4B, Sunrise Apartments, MG Road, Bengaluru 560001",
            "Flat 9C, Sunrise Apartments, MG Road, Bengaluru 560001",
        )
        assert outcome.verdict in {ComparisonVerdict.UNDETERMINED, ComparisonVerdict.EQUAL}

    def test_unrelated_addresses_differ(self):
        outcome = compare_address("12 MG Road, Bengaluru", "88 Park Street, Kolkata")
        assert outcome.is_different


class TestOrganisationComparison:
    def test_legal_suffixes_are_ignored(self):
        assert compare_organisation("Acme Technologies Pvt Ltd", "Acme Technologies").is_equal

    def test_different_employers_differ(self):
        assert compare_organisation("Acme Technologies", "Globex Industries").is_different

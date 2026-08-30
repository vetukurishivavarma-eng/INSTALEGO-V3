"""Identifier normalisation and structural validation."""

from app.utils.identifiers import (
    check_aadhaar,
    check_email,
    check_pan,
    check_phone,
    normalize_account_number,
    normalize_email,
    normalize_phone,
    verhoeff_valid,
)


class TestPan:
    def test_well_formed_pan(self):
        result = check_pan("abcde1234f")
        assert result.valid_format is True
        assert result.normalized == "ABCDE1234F"

    def test_spaces_are_stripped(self):
        assert check_pan("ABCDE 1234 F").normalized == "ABCDE1234F"

    def test_malformed_pan_is_reported_not_corrected(self):
        result = check_pan("ABCD1234F")
        assert result.valid_format is False
        assert result.normalized == "ABCD1234F"


class TestAadhaar:
    def test_display_spacing_is_removed(self):
        # 234567890124 carries the correct Verhoeff check digit for its prefix.
        assert check_aadhaar("2345 6789 0124").normalized == "234567890124"

    def test_checksum_is_enforced(self):
        assert verhoeff_valid("234567890124") is True
        assert verhoeff_valid("234567890123") is False

    def test_a_transposed_pair_is_caught(self):
        # Verhoeff exists to catch exactly this OCR failure mode.
        assert verhoeff_valid("234567890124") is True
        assert verhoeff_valid("234567809124") is False

    def test_bad_checksum_is_described_as_a_possible_misread(self):
        result = check_aadhaar("2345 6789 0123")
        assert result.valid_format is False
        assert "misread" in result.reason

    def test_wrong_length_is_rejected(self):
        assert check_aadhaar("12345").valid_format is False


class TestAccountNumbers:
    def test_leading_zeros_survive(self):
        assert normalize_account_number("000123456789") == "000123456789"

    def test_spacing_is_removed(self):
        assert normalize_account_number("0001 2345 6789") == "000123456789"


class TestPhoneAndEmail:
    def test_country_code_forms_converge(self):
        assert normalize_phone("+91 98765 43210") == "9876543210"
        assert normalize_phone("098765-43210") == "9876543210"
        assert normalize_phone("9876543210") == "9876543210"

    def test_phone_validation(self):
        assert check_phone("+91 9876543210").valid_format is True
        assert check_phone("12345").valid_format is False

    def test_email_case_and_space_normalisation(self):
        assert normalize_email("  Ravi.Kumar@Example.COM ") == "ravi.kumar@example.com"

    def test_email_validation(self):
        assert check_email("ravi@example.com").valid_format is True
        assert check_email("ravi.example.com").valid_format is False

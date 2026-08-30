"""Name handling, similarity and log masking."""

from app.utils.text import (
    initials_expansion_match,
    mask_sensitive,
    mask_value,
    name_tokens,
    normalize_name,
    normalize_text,
    similarity,
    token_set_similarity,
)


class TestNameNormalisation:
    def test_case_and_spacing_differences_collapse(self):
        assert normalize_name("Ravi Kumar") == normalize_name("RAVI   KUMAR")

    def test_titles_are_dropped(self):
        assert normalize_name("Mr. Ravi Kumar") == "RAVI KUMAR"
        assert normalize_name("Smt Anita Devi") == "ANITA DEVI"

    def test_accents_are_folded(self):
        assert normalize_name("José Álvarez") == "JOSE ALVAREZ"

    def test_punctuation_is_removed_from_tokens(self):
        assert name_tokens("Ravi K. Kumar") == ["RAVI", "K", "KUMAR"]

    def test_empty_input_is_safe(self):
        assert normalize_name(None) == ""
        assert name_tokens("") == []


class TestInitialsExpansion:
    def test_middle_initial_matches_absence(self):
        assert initials_expansion_match(name_tokens("Ravi K Kumar"), name_tokens("Ravi Kumar"))

    def test_initial_matches_full_token(self):
        assert initials_expansion_match(name_tokens("R K Kumar"), name_tokens("Ravi Kumar"))

    def test_different_surnames_do_not_match(self):
        assert not initials_expansion_match(name_tokens("Ravi Kumar"), name_tokens("Ravi Sharma"))

    def test_extra_full_name_part_does_not_match(self):
        assert not initials_expansion_match(
            name_tokens("Ravi Prasad Kumar"), name_tokens("Ravi Sharma")
        )


class TestSimilarity:
    def test_identical_after_normalisation_scores_one(self):
        assert similarity("Ravi Kumar", "RAVI KUMAR") == 1.0

    def test_spelling_variant_scores_high(self):
        assert similarity("Ravi Kumar", "Ravi Kumaar") > 0.85

    def test_unrelated_names_score_low(self):
        assert similarity("Ravi Kumar", "Sunita Sharma") < 0.5

    def test_reordered_tokens_match_on_token_set(self):
        assert token_set_similarity("Kumar Ravi", "Ravi Kumar") == 1.0


class TestMasking:
    def test_pan_is_masked_in_free_text(self):
        masked = mask_sensitive("Applicant PAN is ABCDE1234F on page 2")
        assert "ABCDE1234F" not in masked
        assert "[PAN:" in masked

    def test_aadhaar_and_email_are_masked(self):
        masked = mask_sensitive("2345 6789 0123 / ravi@example.com")
        assert "2345 6789 0123" not in masked
        assert "ravi@example.com" not in masked

    def test_mask_value_keeps_the_tail_for_reconciliation(self):
        assert mask_value("123456789012") == "********9012"

    def test_masking_none_is_empty(self):
        assert mask_sensitive(None) == ""

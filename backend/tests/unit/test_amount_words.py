"""Reading an amount written out in words, and checking it against the figure.

Documents state an amount twice on purpose — "Rs. 5,00,000/- (Rupees Five Lakh
only)" — so that a single altered digit does not pass unnoticed. Reading only
the numerals throws that protection away, and the numerals are the easy half to
change. Where the two disagree the words prevail, which is why the finding is
worded as it is rather than left for a reviewer to guess.

The parser returns None rather than a guess. An amount it cannot read is a
REVIEW, not an invented number: that is the whole reason this check is worth
having.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import RuleResult, Severity
from app.utils.numbers import parse_amount_words
from tests.unit.test_land_rules import build_context, findings_of_type, observation, run


class TestReadingTheWords:
    @pytest.mark.parametrize(
        "phrase,expected",
        [
            ("Rupees Five Lakh only", "500000"),
            ("Rupees Forty Two Thousand Only", "42000"),
            ("Five Lakh", "500000"),
            ("Rupees Seven Lakh Fifty Thousand only", "750000"),
            ("One Crore Twenty Five Lakh", "12500000"),
            ("Rupees Twelve Thousand Five Hundred only", "12500"),
            ("Rupees Forty Two Thousand Three Hundred Fifty only", "42350"),
            ("Rupees Five Lakhs Only", "500000"),
            ("Rupees Ten only", "10"),
        ],
    )
    def test_indian_number_words(self, phrase, expected):
        assert parse_amount_words(phrase) == Decimal(expected)

    @pytest.mark.parametrize(
        "phrase",
        [
            "Rupees Five Lakh and Fifty Paise only",
            "Rupees Five Lakh Fifty Paise only",
        ],
    )
    def test_paise_are_not_added_to_the_rupees(self, phrase):
        """Cutting at the word "paise" alone leaves the fifty behind and
        reports 500050."""
        assert parse_amount_words(phrase) == Decimal("500000")

    @pytest.mark.parametrize(
        "phrase",
        [
            "the applicant hereby declares",
            "only",
            "Rs. 5,00,000/-",
            "",
            None,
        ],
    )
    def test_anything_unreadable_returns_none_rather_than_a_guess(self, phrase):
        assert parse_amount_words(phrase) is None

    def test_an_unknown_word_inside_the_phrase_aborts(self):
        """Skipping words it does not know would let the parser read a number
        out of ordinary prose."""
        assert parse_amount_words("Five Lakh subject to conditions") is None


def words_document(figure_field, figure, words, doc_type="AGREEMENT", doc_id="w1"):
    from app.rules import DocumentView

    view = DocumentView(document_id=doc_id, filename="SanctionLetter.pdf",
                        document_type=doc_type, sha256=doc_id * 8, page_count=1)
    fields = [
        observation(figure_field, figure, doc_id, "SanctionLetter.pdf", doc_type),
        observation("amount_in_words", words, doc_id, "SanctionLetter.pdf", doc_type),
    ]
    return view, fields


class TestCheckingTheWordsAgainstTheFigure:
    def test_agreement_between_the_two_raises_nothing(self):
        pair = words_document("loan_amount", "Rs. 5,00,000/-", "Rupees Five Lakh only")
        outcomes, candidates = run(build_context([pair], applicant=None))
        assert findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH") == []
        assert any(
            o.rule_id == "financial.amount_in_words_match" and o.result == RuleResult.PASS
            for o in outcomes
        )

    def test_a_disagreement_is_reported_high(self):
        """An altered digit: the figure says seven lakh fifty, the words five."""
        pair = words_document("loan_amount", "Rs. 7,50,000/-", "Rupees Five Lakh only")
        _, candidates = run(build_context([pair], applicant=None))
        findings = findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_the_finding_says_which_side_governs(self):
        pair = words_document("loan_amount", "Rs. 7,50,000/-", "Rupees Five Lakh only")
        _, candidates = run(build_context([pair], applicant=None))
        summary = findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH")[0].summary
        assert "words" in summary and "governs" in summary
        assert "7,50,000" in summary and "Five Lakh" in summary

    def test_formatting_alone_is_not_a_disagreement(self):
        pair = words_document("loan_amount", "500000.00", "Rupees Five Lakh only")
        _, candidates = run(build_context([pair], applicant=None))
        assert findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH") == []

    def test_the_words_are_checked_against_the_same_document_only(self):
        """A figure on the payslip must not satisfy words on the deed."""
        deed = words_document("property_value", "Rs. 42,00,000",
                              "Rupees Forty Two Lakh only", doc_type="SALE_DEED", doc_id="s1")
        elsewhere = words_document("loan_amount", "Rs. 5,00,000/-",
                                   "Rupees Five Lakh only", doc_id="w2")
        _, candidates = run(build_context([deed, elsewhere], applicant=None))
        assert findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH") == []

    def test_unreadable_words_go_to_review_rather_than_a_finding(self):
        pair = words_document("loan_amount", "Rs. 5,00,000/-", "Rupees Five Lakh subject to")
        outcomes, candidates = run(build_context([pair], applicant=None))
        assert findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH") == []
        assert any(
            o.rule_id == "financial.amount_in_words_match" and o.result == RuleResult.REVIEW
            for o in outcomes
        )

    def test_words_with_no_figure_to_check_are_not_applicable(self):
        from app.rules import DocumentView

        view = DocumentView(document_id="n1", filename="Letter.pdf",
                            document_type="AGREEMENT", sha256="n" * 64, page_count=1)
        fields = [observation("amount_in_words", "Rupees Five Lakh only", "n1",
                              "Letter.pdf", "AGREEMENT")]
        outcomes, candidates = run(build_context([(view, fields)], applicant=None))
        assert findings_of_type(candidates, "AMOUNT_WORDS_FIGURE_MISMATCH") == []
        assert any(
            o.rule_id == "financial.amount_in_words_match"
            and o.result == RuleResult.NOT_APPLICABLE
            for o in outcomes
        )

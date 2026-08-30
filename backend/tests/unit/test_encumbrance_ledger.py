"""The encumbrance certificate read as a table rather than a summary.

An applicant supplies the deed that gave them the property. They do not hold
the deeds of the people who owned it before, and quite reasonably so — which
means a chain built from deeds alone is only ever as long as the paperwork that
happened to survive. The certificate lists every registered transfer over its
period, so a break in the middle of the history shows up here even when no deed
for it was ever supplied. That is the check these tests cover.

The row-classification is the part that most easily goes wrong in a quiet way.
A mortgage, its release and a lease sit in the same table as a sale, and only a
sale moves ownership. Counting a mortgage as a link would break the chain of
every property that ever carried a loan.
"""

from __future__ import annotations

import pytest

from app.models.enums import RuleResult, Severity
from app.rules import LedgerView, load_rule_config
from app.schemas.extraction import EncumbranceLedger, RegisteredTransaction
from tests.unit.test_land_rules import (
    APPLICANT,
    UNBROKEN,
    build_context,
    findings_of_type,
    outcomes_for,
    run,
)


def row(nature, executant, claimant, date):
    return {"nature": nature, "executant": executant, "claimant": claimant, "date": date}


UNBROKEN_LEDGER = [
    row("Sale Deed", "Anil Sharma", "Meera Reddy", "14/03/2015"),
    row("Mortgage", "Meera Reddy", "Canara Bank", "08/01/2017"),
    row("Release of mortgage", "Canara Bank", "Meera Reddy", "22/11/2018"),
    row("Sale Deed", "Meera Reddy", "Suresh Kumar", "02/09/2019"),
    row("Sale Deed", "Suresh Kumar", APPLICANT, "21/06/2024"),
]


def ledger(transactions, name="EncumbranceCertificate.pdf"):
    return LedgerView(
        document_id="e1",
        document_name=name,
        period_from="01/01/2013",
        period_to="01/07/2026",
        transactions=transactions,
    )


def context_with(ledgers, pairs=()):
    ctx = build_context(list(pairs), applicant=APPLICANT)
    ctx.ledgers = ledgers
    return ctx


class TestClassifyingTheRows:
    @pytest.mark.parametrize(
        "nature,moves_ownership",
        [
            ("Sale Deed", True),
            ("Deed of Absolute Sale", True),
            ("Gift Deed", True),
            ("Partition Deed", True),
            ("Mortgage", False),
            ("Simple Mortgage", False),
            ("Release of mortgage", False),
            ("Lease Deed", False),
            ("Charge", False),
            ("Attachment", False),
            ("", False),
        ],
    )
    def test_only_transfers_count_as_links(self, nature, moves_ownership):
        assert RegisteredTransaction(nature=nature).is_transfer is moves_ownership

    def test_a_mortgage_between_two_sales_does_not_break_the_chain(self):
        """The property was mortgaged and released between owners, which is
        entirely ordinary and must not read as a change of hands."""
        _, candidates = run(context_with([ledger(UNBROKEN_LEDGER)]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []


class TestTheChainInsideTheCertificate:
    def test_an_unbroken_ledger_passes(self):
        outcomes, candidates = run(context_with([ledger(UNBROKEN_LEDGER)]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []
        assert any(
            o.result == RuleResult.PASS for o in outcomes_for(outcomes, "land.ledger_chain")
        )

    def test_a_gap_the_deeds_could_not_show_is_found(self):
        """No deed for the missing transfer was ever supplied — this is the
        case a deed-only chain cannot see."""
        gapped = [
            row("Sale Deed", "Anil Sharma", "Meera Reddy", "14/03/2015"),
            row("Sale Deed", "Priya Nair", "Suresh Kumar", "02/09/2019"),
            row("Sale Deed", "Suresh Kumar", APPLICANT, "21/06/2024"),
        ]
        _, candidates = run(context_with([ledger(gapped)]))
        findings = findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert "Meera Reddy" in findings[0].summary
        assert "Priya Nair" in findings[0].summary

    def test_rows_are_walked_in_date_order_not_printed_order(self):
        shuffled = [UNBROKEN_LEDGER[4], UNBROKEN_LEDGER[0], UNBROKEN_LEDGER[3]]
        _, candidates = run(context_with([ledger(shuffled)]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []

    def test_a_nil_certificate_is_not_a_chain(self):
        outcomes, candidates = run(context_with([ledger([])]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []
        assert all(
            o.result == RuleResult.NOT_APPLICABLE
            for o in outcomes_for(outcomes, "land.ledger_chain")
        )

    def test_a_malformed_row_is_skipped_rather_than_crashing(self):
        messy = [*UNBROKEN_LEDGER, {"nature": "Sale Deed"}, {"junk": True}]
        _, candidates = run(context_with([ledger(messy)]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []

    def test_no_certificate_means_the_rule_does_not_apply(self):
        outcomes, _ = run(context_with([], UNBROKEN))
        assert all(
            o.result == RuleResult.NOT_APPLICABLE
            for o in outcomes_for(outcomes, "land.ledger_chain")
        )


class TestTheDeedsAgainstTheRegister:
    def test_deeds_matching_the_register_pass(self):
        outcomes, candidates = run(context_with([ledger(UNBROKEN_LEDGER)], UNBROKEN))
        assert findings_of_type(candidates, "DEED_NOT_IN_ENCUMBRANCE_RECORD") == []
        assert any(
            o.result == RuleResult.PASS
            for o in outcomes_for(outcomes, "land.deeds_agree_with_ledger")
        )

    def test_a_deed_the_registry_never_recorded_is_surfaced(self):
        """An unregistered or forged conveyance looks exactly like this."""
        without_the_last = UNBROKEN_LEDGER[:-1]
        _, candidates = run(context_with([ledger(without_the_last)], UNBROKEN))
        findings = findings_of_type(candidates, "DEED_NOT_IN_ENCUMBRANCE_RECORD")
        assert len(findings) == 1
        assert APPLICANT in findings[0].summary

    def test_it_is_surfaced_for_judgement_rather_than_asserted(self):
        """A deed registered outside the certificate's period is the innocent
        explanation, and it is common."""
        _, candidates = run(context_with([ledger(UNBROKEN_LEDGER[:-1])], UNBROKEN))
        assert findings_of_type(candidates, "DEED_NOT_IN_ENCUMBRANCE_RECORD")[0].needs_reasoning

    def test_it_needs_both_kinds_of_document(self):
        outcomes, _ = run(context_with([ledger(UNBROKEN_LEDGER)]))
        assert all(
            o.result == RuleResult.NOT_APPLICABLE
            for o in outcomes_for(outcomes, "land.deeds_agree_with_ledger")
        )


class TestTheLedgerSchema:
    def test_an_empty_certificate_parses(self):
        parsed = EncumbranceLedger.model_validate({"summary": "NIL", "transactions": []})
        assert parsed.transactions == []
        assert parsed.summary == "NIL"

    def test_unknown_keys_are_ignored_rather_than_rejected(self):
        """A model that volunteers an extra column must not fail the read."""
        parsed = EncumbranceLedger.model_validate(
            {"transactions": [{"nature": "Sale Deed", "executant": "A",
                               "claimant": "B", "volume": "14"}]}
        )
        assert len(parsed.transactions) == 1

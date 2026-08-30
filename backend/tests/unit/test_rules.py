"""Rule engine behaviour, framed as the evaluation cases from the brief."""

from datetime import date

import pytest

from app.agents.profile_builder import collect_candidates, consolidate
from app.models.enums import FieldStatus, OverallStatus, RuleResult, Severity
from app.rules import (
    DocumentView,
    FieldObservation,
    RuleContext,
    RuleEngine,
    decide_status,
    load_rule_config,
)
from app.schemas.discrepancy import DiscrepancyOut

REVIEW_DATE = date(2026, 8, 29)


def observation(field, value, doc_id, doc_name, doc_type, page=1, confidence=0.95):
    return FieldObservation(
        canonical_field=field,
        raw_field=field,
        value=value,
        normalized_value=None,
        confidence=confidence,
        document_id=doc_id,
        document_name=doc_name,
        document_type=doc_type,
        page=page,
        snippet=f"{field}: {value}",
    )


def build_context(observations, documents=None, bank_id="bank_a"):
    config = load_rule_config(bank_id)
    documents = documents or [
        DocumentView(document_id="d1", filename="Aadhaar.pdf", document_type="AADHAAR",
                     sha256="a" * 64, page_count=1),
        DocumentView(document_id="d2", filename="Application.pdf",
                     document_type="LOAN_APPLICATION", sha256="b" * 64, page_count=3),
        DocumentView(document_id="d3", filename="Payslip.pdf", document_type="SALARY_SLIP",
                     sha256="c" * 64, page_count=1),
        DocumentView(document_id="d4", filename="Statement.pdf",
                     document_type="BANK_STATEMENT", sha256="d" * 64, page_count=8),
        DocumentView(document_id="d5", filename="Bill.pdf", document_type="ADDRESS_PROOF",
                     sha256="e" * 64, page_count=1),
    ]
    candidates = collect_candidates(
        [
            {
                "document_id": o.document_id,
                "document_name": o.document_name,
                "document_type": o.document_type,
                "fields": [
                    {
                        "canonical_field": o.canonical_field,
                        "value": o.value,
                        "confidence": o.confidence,
                        "page": o.page,
                        "snippet": o.snippet,
                    }
                ],
            }
            for o in observations
        ]
    )
    return RuleContext(
        profile=consolidate(candidates),
        documents=documents,
        observations=observations,
        config=config,
        as_of=REVIEW_DATE,
    )


def run(context):
    engine = RuleEngine(context.config)
    outcomes = engine.run(context)
    return outcomes, RuleEngine.candidates(outcomes)


def findings_of_type(candidates, type_name):
    return [c for c in candidates if c.type == type_name]


def as_discrepancies(candidates, verified=False):
    return [
        DiscrepancyOut(
            id=f"x{i}",
            code=f"D{i:03d}",
            type=c.type,
            field=c.field,
            severity=c.severity,
            classification="CONFIRMED",
            confidence=0.9,
            origin=c.origin,
            verified=verified,
        )
        for i, c in enumerate(candidates, start=1)
    ]


class TestCase001AllConsistent:
    """Consistent documents produce no findings and a CLEAR status."""

    def observations(self):
        return [
            observation("name", "Ravi Kumar", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("name", "RAVI KUMAR", "d2", "Application.pdf", "LOAN_APPLICATION"),
            observation("date_of_birth", "12/04/1998", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("date_of_birth", "1998-04-12", "d2", "Application.pdf", "LOAN_APPLICATION"),
            observation("current_address", "12 MG Road, Bengaluru 560001", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("current_address", "12, M.G. Road, Bengaluru - 560001", "d5", "Bill.pdf", "ADDRESS_PROOF"),
        ]

    def test_no_discrepancies_are_raised(self):
        _, candidates = run(build_context(self.observations()))
        material = [c for c in candidates if c.type != "MISSING_REQUIRED_DOCUMENT"]
        assert material == [], f"unexpected findings: {[c.type for c in material]}"

    def test_status_is_clear(self):
        context = build_context(self.observations())
        _, candidates = run(context)
        decision = decide_status(as_discrepancies(candidates), [], context.config)
        assert decision.status == OverallStatus.CLEAR
        assert decision.manual_review_required is False

    def test_profile_records_agreement(self):
        context = build_context(self.observations())
        assert context.profile.fields["name"].status == FieldStatus.CONFIRMED
        assert context.profile.fields["date_of_birth"].status == FieldStatus.CONFIRMED


class TestCase002DobMismatch:
    def test_dob_mismatch_is_high(self):
        observations = [
            observation("date_of_birth", "12/04/1998", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("date_of_birth", "12/04/1997", "d2", "Application.pdf", "LOAN_APPLICATION"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "DOB_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert set(findings[0].values) == {"12/04/1998", "12/04/1997"}

    def test_both_values_and_pages_are_preserved_as_evidence(self):
        observations = [
            observation("date_of_birth", "12/04/1998", "d1", "Aadhaar.pdf", "AADHAAR", page=1),
            observation("date_of_birth", "12/04/1997", "d2", "Application.pdf", "LOAN_APPLICATION", page=3),
        ]
        _, candidates = run(build_context(observations))
        evidence = findings_of_type(candidates, "DOB_MISMATCH")[0].evidence
        assert {(e.document_name, e.page, e.value) for e in evidence} == {
            ("Aadhaar.pdf", 1, "12/04/1998"),
            ("Application.pdf", 3, "12/04/1997"),
        }

    def test_profile_marks_the_field_conflicting_and_keeps_both(self):
        context = build_context(
            [
                observation("date_of_birth", "12/04/1998", "d1", "Aadhaar.pdf", "AADHAAR"),
                observation("date_of_birth", "12/04/1997", "d2", "Application.pdf", "LOAN_APPLICATION"),
            ]
        )
        field = context.profile.fields["date_of_birth"]
        assert field.status == FieldStatus.CONFLICTING
        assert sorted(field.candidates) == ["12/04/1997", "12/04/1998"]


class TestCase003PanMismatch:
    def test_pan_mismatch_is_high_and_needs_no_model(self):
        observations = [
            observation("pan", "ABCDE1234F", "d1", "PAN.pdf", "PAN"),
            observation("pan", "ABCDE1234G", "d2", "Application.pdf", "LOAN_APPLICATION"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "PAN_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        # An exact identifier mismatch is settled; spending a model call on it
        # would add cost and risk without adding information.
        assert findings[0].needs_reasoning is False
        assert findings[0].deterministic is True


class TestCase004NameVariation:
    def test_middle_initial_is_not_a_discrepancy(self):
        observations = [
            observation("name", "Ravi Kumar", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("name", "Ravi K Kumar", "d2", "Application.pdf", "LOAN_APPLICATION"),
        ]
        outcomes, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "NAME_MISMATCH") == []
        name_outcomes = [o for o in outcomes if o.rule_id == "identity.name_match"]
        assert any(o.result == RuleResult.PASS for o in name_outcomes)

    def test_a_genuinely_different_name_is_flagged(self):
        observations = [
            observation("name", "Ravi Kumar", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("name", "Sunita Sharma", "d2", "Application.pdf", "LOAN_APPLICATION"),
        ]
        _, candidates = run(build_context(observations))
        assert len(findings_of_type(candidates, "NAME_MISMATCH")) == 1


class TestCase005MissingIncomeProof:
    def test_missing_requirement_is_detected_deterministically(self):
        documents = [
            DocumentView(document_id="d1", filename="Aadhaar.pdf", document_type="AADHAAR",
                         sha256="a" * 64),
            DocumentView(document_id="d4", filename="Statement.pdf",
                         document_type="BANK_STATEMENT", sha256="d" * 64),
        ]
        context = build_context([], documents=documents)
        _, candidates = run(context)
        missing = {c.field for c in findings_of_type(candidates, "MISSING_REQUIRED_DOCUMENT")}
        assert missing == {"INCOME_PROOF"}

    def test_aadhaar_satisfies_both_identity_and_address(self):
        documents = [
            DocumentView(document_id="d1", filename="Aadhaar.pdf", document_type="AADHAAR",
                         sha256="a" * 64),
        ]
        _, candidates = run(build_context([], documents=documents))
        missing = {c.field for c in findings_of_type(candidates, "MISSING_REQUIRED_DOCUMENT")}
        assert "IDENTITY_PROOF" not in missing
        assert "ADDRESS_PROOF" not in missing

    def test_bank_b_requires_more_documents(self):
        documents = [
            DocumentView(document_id="d1", filename="Aadhaar.pdf", document_type="AADHAAR",
                         sha256="a" * 64),
        ]
        _, candidates = run(build_context([], documents=documents, bank_id="bank_b"))
        missing = {c.field for c in findings_of_type(candidates, "MISSING_REQUIRED_DOCUMENT")}
        assert "PROPERTY_DOCUMENT" in missing
        assert "LOAN_APPLICATION" in missing


class TestCase006AddressVariation:
    def test_formatting_variation_is_not_flagged(self):
        observations = [
            observation("current_address", "12 MG Road, Bengaluru 560001", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("current_address", "12, M.G. Road, Bengaluru - 560001", "d5", "Bill.pdf", "ADDRESS_PROOF"),
        ]
        _, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "ADDRESS_MISMATCH") == []

    def test_a_different_address_is_medium_for_bank_a(self):
        observations = [
            observation("current_address", "12 MG Road, Bengaluru 560001", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("current_address", "88 Park Street, Kolkata 700016", "d5", "Bill.pdf", "ADDRESS_PROOF"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "ADDRESS_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM

    def test_the_same_mismatch_is_high_for_bank_b(self):
        observations = [
            observation("current_address", "12 MG Road, Bengaluru 560001", "d1", "Aadhaar.pdf", "AADHAAR"),
            observation("current_address", "88 Park Street, Kolkata 700016", "d5", "Bill.pdf", "ADDRESS_PROOF"),
        ]
        _, candidates = run(build_context(observations, bank_id="bank_b"))
        findings = findings_of_type(candidates, "ADDRESS_MISMATCH")
        assert findings[0].severity == Severity.HIGH


class TestDateAndFinancialRules:
    def test_expired_identity_document_is_high(self):
        observations = [
            observation("date_of_expiry", "01/01/2020", "d6", "Passport.pdf", "PASSPORT"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "EXPIRED_DOCUMENT")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_valid_document_is_not_flagged(self):
        observations = [
            observation("date_of_expiry", "01/01/2030", "d6", "Passport.pdf", "PASSPORT"),
        ]
        _, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "EXPIRED_DOCUMENT") == []

    def test_unreadable_expiry_is_review_not_a_failure(self):
        observations = [
            observation("date_of_expiry", "illegible", "d6", "Passport.pdf", "PASSPORT"),
        ]
        outcomes, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "EXPIRED_DOCUMENT") == []
        assert findings_of_type(candidates, "EXPIRY_UNREADABLE")

    def test_loan_amount_mismatch_is_high(self):
        observations = [
            observation("loan_amount", "Rs. 5,00,000", "d2", "Application.pdf", "LOAN_APPLICATION"),
            observation("loan_amount", "750000", "d7", "Sanction.pdf", "AGREEMENT"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "LOAN_AMOUNT_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_loan_amount_formatting_difference_is_not_a_finding(self):
        observations = [
            observation("loan_amount", "Rs. 5,00,000/-", "d2", "Application.pdf", "LOAN_APPLICATION"),
            observation("loan_amount", "500000.00", "d7", "Sanction.pdf", "AGREEMENT"),
        ]
        _, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "LOAN_AMOUNT_MISMATCH") == []

    def test_loan_amount_mismatch_survives_a_word_form_restatement(self):
        """The regression the degradation sweep found.

        A sanction letter writes the figure and then repeats it in words. That
        phrasing used to defeat the amount parser, and an amount that cannot be
        parsed is reported NOT_COMPARABLE and skipped without a finding — so
        this mismatch was silently invisible rather than merely uncertain.
        """
        observations = [
            observation("loan_amount", "Rs. 7,50,000", "d2", "Application.pdf",
                        "LOAN_APPLICATION"),
            observation("loan_amount", "Rs. 5,00,000/- (Rupees Five Lakh only)", "d7",
                        "Sanction.pdf", "AGREEMENT"),
        ]
        _, candidates = run(build_context(observations))
        findings = findings_of_type(candidates, "LOAN_AMOUNT_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_agreeing_amounts_in_the_two_conventions_are_not_a_finding(self):
        observations = [
            observation("loan_amount", "500000.00", "d2", "Application.pdf",
                        "LOAN_APPLICATION"),
            observation("loan_amount", "Rs. 5,00,000/- (Rupees Five Lakh only)", "d7",
                        "Sanction.pdf", "AGREEMENT"),
        ]
        _, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "LOAN_AMOUNT_MISMATCH") == []

    def test_income_within_bank_tolerance_is_not_a_finding(self):
        # bank_a allows 10%.
        observations = [
            observation("income", "50000", "d3", "Payslip.pdf", "SALARY_SLIP"),
            observation("income", "52000", "d8", "ITR.pdf", "ITR"),
        ]
        _, candidates = run(build_context(observations))
        assert findings_of_type(candidates, "INCOME_MISMATCH") == []

    def test_the_same_income_gap_exceeds_bank_b_tolerance(self):
        observations = [
            observation("income", "50000", "d3", "Payslip.pdf", "SALARY_SLIP"),
            observation("income", "52000", "d8", "ITR.pdf", "ITR"),
        ]
        _, candidates = run(build_context(observations, bank_id="bank_b"))
        assert len(findings_of_type(candidates, "INCOME_MISMATCH")) == 1


class TestDocumentQualityRules:
    def test_duplicate_uploads_are_detected_by_hash(self):
        documents = [
            DocumentView(document_id="d1", filename="Aadhaar.pdf", document_type="AADHAAR",
                         sha256="same" * 16),
            DocumentView(document_id="d2", filename="Aadhaar-copy.pdf", document_type="AADHAAR",
                         sha256="same" * 16),
        ]
        _, candidates = run(build_context([], documents=documents))
        findings = findings_of_type(candidates, "DUPLICATE_DOCUMENT")
        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW

    def test_unclassified_documents_do_not_satisfy_requirements(self):
        documents = [
            DocumentView(document_id="d1", filename="scan.pdf", document_type="UNKNOWN",
                         sha256="f" * 64),
        ]
        _, candidates = run(build_context([], documents=documents))
        assert findings_of_type(candidates, "UNCLASSIFIED_DOCUMENT")
        missing = {c.field for c in findings_of_type(candidates, "MISSING_REQUIRED_DOCUMENT")}
        assert "IDENTITY_PROOF" in missing

    def test_wording_never_alleges_fraud(self):
        documents = [
            DocumentView(document_id="d1", filename="scan.pdf", document_type="UNKNOWN",
                         sha256="f" * 64, is_readable=False, error_code="OCR_FAILED"),
        ]
        _, candidates = run(build_context([], documents=documents))
        banned = ("fraud", "forged", "fake", "falsified")
        for candidate in candidates:
            text = f"{candidate.summary} {candidate.type}".lower()
            assert not any(word in text for word in banned), candidate.summary


class TestStatusPolicy:
    def test_verified_high_is_high_risk(self):
        config = load_rule_config("bank_a")
        findings = [
            DiscrepancyOut(id="1", code="D001", type="DOB_MISMATCH", field="date_of_birth",
                           severity=Severity.HIGH, classification="CONFIRMED", confidence=0.95,
                           origin="RULE_ENGINE", verified=True)
        ]
        assert decide_status(findings, [], config).status == OverallStatus.HIGH_RISK

    def test_unverified_high_is_review_required(self):
        config = load_rule_config("bank_a")
        findings = [
            DiscrepancyOut(id="1", code="D001", type="DOB_MISMATCH", field="date_of_birth",
                           severity=Severity.HIGH, classification="CONFIRMED", confidence=0.95,
                           origin="RULE_ENGINE", verified=False)
        ]
        assert decide_status(findings, [], config).status == OverallStatus.REVIEW_REQUIRED

    def test_only_low_findings_stay_clear(self):
        config = load_rule_config("bank_a")
        findings = [
            DiscrepancyOut(id="1", code="D001", type="DUPLICATE_DOCUMENT", severity=Severity.LOW,
                           classification="CONFIRMED", confidence=0.9, origin="RULE_ENGINE")
        ]
        decision = decide_status(findings, [], config)
        assert decision.status == OverallStatus.CLEAR
        assert "none are material" in " ".join(decision.reasons)


@pytest.mark.parametrize("bank_id", ["default", "bank_a", "bank_b"])
def test_every_rule_reports_an_outcome(bank_id):
    """A rule that silently produces nothing is indistinguishable from a rule
    that did not run, so each must report at least NOT_APPLICABLE."""
    context = build_context([], bank_id=bank_id)
    outcomes, _ = run(context)
    reported = {o.rule_id for o in outcomes}
    from app.rules import registered_rules

    expected = {f"{category}.{name}" for category, names in registered_rules().items() for name in names}
    assert expected <= reported, f"no outcome from: {sorted(expected - reported)}"

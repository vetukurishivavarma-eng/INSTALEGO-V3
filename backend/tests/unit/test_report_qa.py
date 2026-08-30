"""Report QA: what it must catch, and what it must stop inventing.

QA is the last gate before a report reaches a reviewer, so both directions
matter equally. A dropped field has to be caught. And a field the bank's
template never asked for must not be reported as dropped — that was the state
of things until now, and it meant every bank_a report came back qa_passed=false
with four fabricated errors. A report that always fails QA teaches reviewers to
ignore QA, which costs more than the check was ever worth.

The question "does this field belong in the report" is answerable exactly, from
the template, in Python. It was being asked of a model that had never been
shown the template.
"""

from __future__ import annotations

import pytest

from app.agents.qa_agent import deterministic_qa
from app.models.enums import Severity
from app.schemas.applicant import ApplicantProfileSchema, ProfileField
from app.schemas.report import CanonicalAnalysis

# bank_a deliberately publishes a narrower profile than the analysis carries.
BANK_A_FIELDS = ["name", "date_of_birth", "pan", "current_address"]
TEMPLATE = {
    "template_id": "bank_a",
    "sections": [
        {"key": "applicant_profile", "include_fields": BANK_A_FIELDS},
        {"key": "documents", "type": "table"},
    ],
}


def analysis_with(**values: str) -> CanonicalAnalysis:
    return CanonicalAnalysis(
        case_id="c1",
        applicant=ApplicantProfileSchema(
            fields={
                name: ProfileField(field=name, value=value, status="CONFIRMED")
                for name, value in values.items()
            }
        ),
    )


FULL_ANALYSIS = analysis_with(
    name="RAVI KUMAR",
    date_of_birth="12/04/1998",
    pan="ABCDE1234F",
    current_address="12, M.G. Road, Bengaluru - 560001",
    # Present in the analysis, deliberately absent from bank_a's template.
    gender="Male",
    father_name="SURESH KUMAR",
    designation="Senior Engineer",
    bank_account="000123456789",
)


def report_with(fields: dict[str, str]) -> dict:
    return {"applicant_profile": {k: {"value": v} for k, v in fields.items()}}


def errors_of_type(errors, type_name):
    return [e for e in errors if e.type == type_name]


class TestFieldsTheTemplateExcluded:
    def test_a_narrower_template_does_not_produce_errors(self):
        """The exact case that was failing: bank_a publishes four profile
        fields, the analysis holds eight, and nothing is wrong."""
        report = report_with({name: FULL_ANALYSIS.applicant.value_of(name)
                              for name in BANK_A_FIELDS})
        errors = deterministic_qa(FULL_ANALYSIS, report, TEMPLATE)
        assert errors_of_type(errors, "MISSING_FIELD") == [], errors

    @pytest.mark.parametrize("excluded", ["gender", "father_name", "designation",
                                          "bank_account"])
    def test_no_excluded_field_is_reported_as_missing(self, excluded):
        report = report_with({name: FULL_ANALYSIS.applicant.value_of(name)
                              for name in BANK_A_FIELDS})
        errors = deterministic_qa(FULL_ANALYSIS, report, TEMPLATE)
        assert not any(excluded in (e.field or "") for e in errors)


class TestFieldsTheTemplateAskedFor:
    def test_a_dropped_field_is_still_caught(self):
        """The check the model was standing in for, now asked correctly."""
        report = report_with({"name": "RAVI KUMAR", "date_of_birth": "12/04/1998",
                              "current_address": "12, M.G. Road, Bengaluru - 560001"})
        errors = errors_of_type(deterministic_qa(FULL_ANALYSIS, report, TEMPLATE),
                                "MISSING_FIELD")
        assert len(errors) == 1
        assert errors[0].field == "applicant_profile.pan"
        assert errors[0].severity == Severity.HIGH

    def test_a_blank_value_counts_as_dropped(self):
        report = report_with({"name": "RAVI KUMAR", "date_of_birth": "12/04/1998",
                              "pan": "", "current_address": "12 MG Road"})
        errors = errors_of_type(deterministic_qa(FULL_ANALYSIS, report, TEMPLATE),
                                "MISSING_FIELD")
        assert [e.field for e in errors] == ["applicant_profile.pan"]

    def test_not_available_counts_as_dropped(self):
        report = report_with({"name": "RAVI KUMAR", "date_of_birth": "12/04/1998",
                              "pan": "NOT_AVAILABLE", "current_address": "12 MG Road"})
        assert errors_of_type(deterministic_qa(FULL_ANALYSIS, report, TEMPLATE),
                              "MISSING_FIELD")

    def test_a_field_the_analysis_never_found_is_not_a_defect(self):
        """The template asks for it, the documents did not supply it. Absent
        from both, so nothing was dropped."""
        analysis = analysis_with(name="RAVI KUMAR", date_of_birth="12/04/1998")
        report = report_with({"name": "RAVI KUMAR", "date_of_birth": "12/04/1998"})
        assert errors_of_type(deterministic_qa(analysis, report, TEMPLATE),
                              "MISSING_FIELD") == []


class TestWithoutATemplate:
    def test_no_template_means_the_check_is_skipped_not_guessed(self):
        """Without the template there is no correct answer, so the check
        declines rather than falling back to comparing against the analysis —
        which is exactly the mistake being fixed."""
        report = report_with({"name": "RAVI KUMAR"})
        assert errors_of_type(deterministic_qa(FULL_ANALYSIS, report, None),
                              "MISSING_FIELD") == []


class TestTheOtherDeterministicChecks:
    def test_an_altered_identifier_is_still_caught(self):
        report = report_with({name: FULL_ANALYSIS.applicant.value_of(name)
                              for name in BANK_A_FIELDS})
        report["applicant_profile"]["pan"] = {"value": "ABCDE1234G"}
        errors = errors_of_type(deterministic_qa(FULL_ANALYSIS, report, TEMPLATE),
                                "IDENTIFIER_ALTERED")
        assert len(errors) == 1
        assert errors[0].severity == Severity.HIGH

    def test_a_contradicted_status_is_still_caught(self):
        report = report_with({name: FULL_ANALYSIS.applicant.value_of(name)
                              for name in BANK_A_FIELDS})
        report["case_summary"] = {"overall_status": "CLEAR"}
        analysis = FULL_ANALYSIS.model_copy(update={"final_status": "HIGH_RISK"})
        assert errors_of_type(deterministic_qa(analysis, report, TEMPLATE), "STATUS_CHANGED")


class TestLowClassificationConfidence:
    """A borderline type must not be presented as a fact.

    The classifier is not reproducible run to run — the same letter came back
    AGREEMENT once and LOAN_APPLICATION the next time, at temperature 0 — and
    the type is not merely a label: it selects which fields are extracted and
    which bank requirement the document is counted against. Nothing can make
    the label deterministic. What this covers is that a close call says so.
    """

    def _context(self, confidence: float):
        from app.models.enums import QualityFlag
        from tests.unit.test_land_rules import build_context
        from app.rules import DocumentView

        flags = [QualityFlag.LOW_CLASSIFICATION_CONFIDENCE] if confidence < 0.70 else []
        view = DocumentView(
            document_id="c1",
            filename="SanctionLetter.pdf",
            document_type="AGREEMENT",
            sha256="c" * 64,
            page_count=1,
            classification_confidence=confidence,
            quality_flags=flags,
        )
        return build_context([(view, [])], applicant=None)

    def test_a_close_call_is_surfaced(self):
        from tests.unit.test_land_rules import findings_of_type, run

        _, candidates = run(self._context(0.55))
        findings = findings_of_type(candidates, "LOW_CLASSIFICATION_CONFIDENCE")
        assert len(findings) == 1
        assert "AGREEMENT" in findings[0].summary
        assert "0.55" in findings[0].summary

    def test_the_finding_explains_why_the_type_matters(self):
        from tests.unit.test_land_rules import findings_of_type, run

        _, candidates = run(self._context(0.55))
        summary = findings_of_type(candidates, "LOW_CLASSIFICATION_CONFIDENCE")[0].summary
        assert "which fields are extracted" in summary

    def test_a_confident_classification_raises_nothing(self):
        from tests.unit.test_land_rules import findings_of_type, run

        _, candidates = run(self._context(0.95))
        assert findings_of_type(candidates, "LOW_CLASSIFICATION_CONFIDENCE") == []

    def test_it_is_low_severity_not_a_defect_in_the_application(self):
        """Nothing is wrong with the applicant. The system is reporting its own
        uncertainty, which is worth saying and not worth blocking on."""
        from app.models.enums import Severity
        from tests.unit.test_land_rules import findings_of_type, run

        _, candidates = run(self._context(0.55))
        assert findings_of_type(candidates, "LOW_CLASSIFICATION_CONFIDENCE")[0].severity == (
            Severity.LOW
        )

    def test_the_document_is_marked_for_review(self):
        """The quality status is what the UI colours, so the flag has to reach
        it or the finding is the only trace."""
        from app.models.enums import DocumentQualityStatus, QualityFlag
        from app.workflows.extraction_workflow import _quality_status

        class _Doc:
            quality_flags = [QualityFlag.LOW_CLASSIFICATION_CONFIDENCE]
            document_type = "AGREEMENT"
            is_readable = True

        class _Parsed:
            is_readable = True

        assert _quality_status(_Doc(), _Parsed()) == DocumentQualityStatus.REVIEW_REQUIRED

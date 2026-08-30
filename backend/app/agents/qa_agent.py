"""Final quality assurance over the generated report.

Two passes run, and the deterministic one matters more. Python compares the
report against the canonical analysis directly: every HIGH finding present,
every identifier character-identical, every severity unchanged, every amount
unaltered. Those are checks with right answers, so they are not delegated.

The agent then reads for the things a diff cannot see — a conclusion the
evidence does not support, a sentence that overstates a finding. It may report
errors; it may not invent findings, and its verdict cannot clear a report that
the deterministic pass failed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base_agent import TEMPERATURE_MAPPING, AgentRun, BaseAgent, clip, render
from app.models.enums import Severity
from app.schemas.report import CanonicalAnalysis, QAError, QAResult

logger = logging.getLogger(__name__)

MAX_PAYLOAD_CHARS = 8000


def deterministic_qa(analysis: CanonicalAnalysis, report: dict[str, Any]) -> list[QAError]:
    """Checks with a correct answer, run in Python.

    Anything found here is a real defect in report generation, not a matter of
    opinion, so each one is HIGH and forces regeneration.
    """
    errors: list[QAError] = []

    reported = report.get("discrepancies") or []
    reported_ids = {str(item.get("id", "")) for item in reported}

    for finding in analysis.high_severity():
        if finding.code not in reported_ids:
            errors.append(
                QAError(
                    type="MISSING_HIGH_FINDING",
                    field=finding.field or finding.type,
                    description=(
                        f"HIGH severity finding {finding.code} ({finding.type}) is in the "
                        "analysis but absent from the report"
                    ),
                    severity=Severity.HIGH,
                )
            )

    severity_by_code = {d.code: str(d.severity) for d in analysis.discrepancies}
    for item in reported:
        code = str(item.get("id", ""))
        expected = severity_by_code.get(code)
        actual = str(item.get("severity", ""))
        if expected and actual and expected != actual:
            errors.append(
                QAError(
                    type="SEVERITY_CHANGED",
                    field=code,
                    description=f"severity for {code} became {actual}, analysis says {expected}",
                    severity=Severity.HIGH,
                )
            )
        if code and expected is None:
            errors.append(
                QAError(
                    type="UNSUPPORTED_FINDING",
                    field=code,
                    description=f"the report contains finding {code}, which is not in the analysis",
                    severity=Severity.HIGH,
                )
            )

    errors.extend(_identifier_errors(analysis, report))

    summary = report.get("case_summary") or {}
    if summary.get("overall_status") and summary["overall_status"] != str(analysis.final_status):
        errors.append(
            QAError(
                type="STATUS_CHANGED",
                field="overall_status",
                description=(
                    f"report status {summary['overall_status']} contradicts the analysis "
                    f"status {analysis.final_status}"
                ),
                severity=Severity.HIGH,
            )
        )

    return errors


def _identifier_errors(analysis: CanonicalAnalysis, report: dict[str, Any]) -> list[QAError]:
    """Identifiers must survive the mapping character for character."""
    errors: list[QAError] = []
    profile = report.get("applicant_profile") or {}
    for field_name in ("pan", "aadhaar", "passport", "driving_license", "bank_account"):
        expected = analysis.applicant.value_of(field_name)
        if not expected:
            continue
        raw = profile.get(field_name)
        actual = raw.get("value") if isinstance(raw, dict) else raw
        if actual in (None, "", "NOT_AVAILABLE"):
            continue
        if str(actual).strip() != expected.strip():
            errors.append(
                QAError(
                    type="IDENTIFIER_ALTERED",
                    field=field_name,
                    description=(
                        f"{field_name} appears as {actual} in the report but {expected} "
                        "in the analysis"
                    ),
                    severity=Severity.HIGH,
                )
            )
    return errors


class QAAgent(BaseAgent):
    prompt_name = "qa_agent"
    temperature = TEMPERATURE_MAPPING

    def review(
        self, analysis: CanonicalAnalysis, report: dict[str, Any]
    ) -> AgentRun[QAResult]:
        system = render(
            self.system_prompt,
            {
                "analysis": clip(
                    json.dumps(_analysis_digest(analysis), separators=(",", ":")),
                    MAX_PAYLOAD_CHARS,
                ),
                "report": clip(json.dumps(report, separators=(",", ":"), default=str),
                               MAX_PAYLOAD_CHARS),
            },
        )
        prompt = (
            "Review the report against the analysis. Report only defects you can point at "
            "in the two documents supplied. Do not add findings about the applicant."
        )
        return self._run(QAResult, prompt=prompt, system=system)


def _analysis_digest(analysis: CanonicalAnalysis) -> dict[str, Any]:
    """A compact analysis for the QA prompt: enough to check, not the world."""
    return {
        "case_id": analysis.case_id,
        "final_status": str(analysis.final_status),
        "applicant": {
            name: {
                "value": field.value,
                "status": str(field.status),
                "candidates": field.candidates,
            }
            for name, field in analysis.applicant.fields.items()
            if field.is_present
        },
        "discrepancies": [
            {
                "id": d.code,
                "type": d.type,
                "field": d.field,
                "severity": str(d.severity),
                "classification": str(d.classification),
            }
            for d in analysis.discrepancies
        ],
        "missing_documents": [m.document_type for m in analysis.missing_documents],
    }


def combine_qa(deterministic: list[QAError], agent: QAResult | None) -> QAResult:
    """Merge both passes. The deterministic result can only tighten the verdict."""
    errors = list(deterministic)
    if agent is not None:
        errors.extend(agent.errors)

    high = [error for error in errors if error.severity == Severity.HIGH]
    return QAResult(
        passed=not errors,
        errors=errors,
        requires_regeneration=bool(high),
    )

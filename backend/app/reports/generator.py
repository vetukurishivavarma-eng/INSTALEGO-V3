"""Turning the canonical analysis into report JSON.

Every section a bank template can declare has a builtin producer here: a plain
function that reads the analysis and writes the section. That is what makes
report content deterministic — the same analysis produces byte-identical JSON
every time, and no sentence in a report comes from a model.

The mapping agent is reached only for a section marked ``"mapping": "agent"``,
which means the template asked for something this system has no field for.
Even then its output is filtered to the declared keys.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import BACKEND_ROOT, settings
from app.models.enums import Severity
from app.schemas.report import CanonicalAnalysis

logger = logging.getLogger(__name__)

NOT_AVAILABLE = "NOT_AVAILABLE"
BUNDLED_TEMPLATES = BACKEND_ROOT / "app" / "reports" / "templates"


class TemplateNotFoundError(LookupError):
    pass


@lru_cache(maxsize=16)
def load_template(template_id: str) -> dict[str, Any]:
    """Configured templates win; the bundled copies are the fallback.

    A missing bank template falls back to the default rather than failing the
    report, since a report with a plain layout is more useful than none.
    """
    for directory in (settings.report_template_dir, BUNDLED_TEMPLATES):
        path = Path(directory) / f"{template_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    if template_id != "default":
        logger.warning("report template %r not found; falling back to default", template_id)
        return load_template("default")
    raise TemplateNotFoundError("the default report template is missing")


def reset_template_cache() -> None:
    load_template.cache_clear()


def build_report(
    analysis: CanonicalAnalysis,
    *,
    template_id: str = "default",
    mapper_agent=None,  # noqa: ANN001 - injected only for agent-mapped sections
) -> dict[str, Any]:
    """Produce the report payload for a template."""
    template = load_template(template_id)
    report: dict[str, Any] = {
        "template_id": template.get("template_id", template_id),
        "title": template.get("title", "Document Verification Report"),
        "subtitle": template.get("subtitle", ""),
        "disclaimer": template.get("disclaimer", ""),
    }

    for section in template.get("sections", []):
        key = section.get("key")
        if not key:
            continue
        producer = _PRODUCERS.get(key)

        if producer is not None and section.get("mapping", "builtin") == "builtin":
            report[key] = producer(analysis, section)
            continue

        if section.get("mapping") == "agent":
            report[key] = _agent_section(section, analysis, mapper_agent)
            continue

        logger.warning("no producer for report section %r; emitting an empty section", key)
        report[key] = {}

    return report


# --------------------------------------------------------------------------
# Builtin section producers
# --------------------------------------------------------------------------
def _case_summary(analysis: CanonicalAnalysis, section: dict[str, Any]) -> dict[str, Any]:
    name_field = analysis.applicant.get("name")
    return {
        "applicant_name": (name_field.value if name_field and name_field.is_present else NOT_AVAILABLE),
        "case_id": analysis.case_ref or analysis.case_id,
        "bank_id": analysis.bank_id or NOT_AVAILABLE,
        "documents_received": len(analysis.documents),
        "documents_expected": len(analysis.documents) + len(analysis.missing_documents),
        "overall_status": str(analysis.final_status),
        "overall_confidence": analysis.overall_confidence,
        "manual_review_required": analysis.manual_review_required,
        "generated_at": (analysis.versions.generated_at or datetime.now(UTC)).isoformat(),
        "counts": analysis.counts(),
    }


def _applicant_profile(analysis: CanonicalAnalysis, section: dict[str, Any]) -> dict[str, Any]:
    """Each field carries its status and its sources, not just a value.

    A report that prints only the value hides the fact that two documents
    disagreed, which is the single most important thing a reviewer needs.
    """
    included = section.get("include_fields") or list(analysis.applicant.fields)
    output: dict[str, Any] = {}

    for name in included:
        field = analysis.applicant.get(name)
        if field is None or not field.is_present:
            output[name] = {"value": NOT_AVAILABLE, "status": "NOT_FOUND", "sources": []}
            continue
        output[name] = {
            "value": field.value or NOT_AVAILABLE,
            "status": str(field.status),
            "confidence": field.confidence,
            "candidates": field.candidates if len(field.candidates) > 1 else [],
            "sources": [
                {
                    "document": source.document_name,
                    "document_type": source.document_type,
                    "page": source.page,
                    "value": source.value,
                }
                for source in field.sources
            ],
        }
    return output


def _documents(analysis: CanonicalAnalysis, section: dict[str, Any]) -> list[dict[str, Any]]:
    columns = [column["key"] for column in section.get("columns", [])]
    rows = []
    for document in analysis.documents:
        payload = document.model_dump()
        payload["quality_flags"] = ", ".join(document.quality_flags) or "none"
        rows.append({key: payload.get(key, NOT_AVAILABLE) for key in columns} if columns else payload)
    return rows


def _discrepancies(analysis: CanonicalAnalysis, section: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per finding, with both sides of the comparison spelled out."""
    rows = []
    order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}

    for finding in sorted(analysis.discrepancies, key=lambda d: order.get(d.severity, 3)):
        evidence = finding.evidence
        first = evidence[0] if evidence else None
        second = evidence[1] if len(evidence) > 1 else None

        rows.append(
            {
                "id": finding.code,
                "type": finding.type,
                "field": finding.field or NOT_AVAILABLE,
                "severity": str(finding.severity),
                "status": str(finding.classification),
                "document_1": first.document_name if first else NOT_AVAILABLE,
                "page_1": first.page if first else 0,
                "value_1": first.value if first else NOT_AVAILABLE,
                "document_2": second.document_name if second else NOT_AVAILABLE,
                "page_2": second.page if second else 0,
                "value_2": second.value if second else NOT_AVAILABLE,
                "confidence": finding.confidence,
                "explanation": finding.explanation or "",
                "recommended_action": finding.recommended_action or "Verify manually.",
                "verified": finding.verified,
                "rule_id": finding.rule_id or NOT_AVAILABLE,
                "evidence": [
                    {
                        "document": ref.document_name,
                        "document_id": ref.document_id,
                        "page": ref.page,
                        "value": ref.value,
                        "snippet": ref.snippet,
                    }
                    for ref in evidence
                ],
            }
        )
    return rows


def _missing_documents(analysis: CanonicalAnalysis, section: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "document_type": item.document_type,
            "severity": str(item.severity),
            "reason": item.reason,
            "required_by": item.required_by,
        }
        for item in analysis.missing_documents
    ]


def _document_quality(analysis: CanonicalAnalysis, section: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "filename": document.filename,
            "quality_status": document.quality_status,
            "quality_flags": ", ".join(document.quality_flags) or "none",
            "quality_notes": document.quality_notes or "",
        }
        for document in analysis.document_quality
    ]


def _validation_results(analysis: CanonicalAnalysis, section: dict[str, Any]) -> list[dict[str, Any]]:
    included = set(section.get("include_results") or ["FAIL", "REVIEW", "PASS"])
    return [
        {
            "rule_id": validation.rule_id,
            "rule_category": validation.rule_category,
            "result": str(validation.result),
            "field": validation.field or NOT_AVAILABLE,
            "severity": str(validation.severity) if validation.severity else NOT_AVAILABLE,
            "reason": validation.reason or "",
        }
        for validation in analysis.validations
        if str(validation.result) in included
    ]


def _final_assessment(analysis: CanonicalAnalysis, section: dict[str, Any]) -> dict[str, Any]:
    """Findings summarised by quoting them, never by re-describing them."""
    high = analysis.high_severity()
    key_findings = [
        f"{finding.code}: {finding.type.replace('_', ' ').lower()} ({finding.severity})"
        for finding in high
    ]
    key_findings += [
        f"{item.document_type} is missing" for item in analysis.missing_documents
    ]

    actions: list[str] = []
    for finding in analysis.discrepancies:
        if finding.severity in {Severity.HIGH, Severity.MEDIUM} and finding.recommended_action:
            action = f"{finding.code}: {finding.recommended_action}"
            if action not in actions:
                actions.append(action)
    if analysis.missing_documents:
        actions.append(
            "Obtain the missing documents listed above before completing the assessment."
        )
    if not actions:
        actions.append("No action required beyond routine review.")

    return {
        "status": str(analysis.final_status),
        "key_findings": key_findings or ["No high severity findings were identified."],
        "recommended_actions": actions,
        "manual_review_required": analysis.manual_review_required,
    }


def _provenance(analysis: CanonicalAnalysis, section: dict[str, Any]) -> dict[str, Any]:
    versions = analysis.versions
    return {
        "analysis_version": versions.analysis_version or NOT_AVAILABLE,
        "model": versions.model or NOT_AVAILABLE,
        "prompt_version": versions.prompt_version or NOT_AVAILABLE,
        "rules_version": versions.rules_version or NOT_AVAILABLE,
        "generated_at": (versions.generated_at or datetime.now(UTC)).isoformat(),
    }


_PRODUCERS = {
    "case_summary": _case_summary,
    "applicant_profile": _applicant_profile,
    "documents": _documents,
    "discrepancies": _discrepancies,
    "missing_documents": _missing_documents,
    "document_quality": _document_quality,
    "validation_results": _validation_results,
    "final_assessment": _final_assessment,
    "provenance": _provenance,
}


def _agent_section(
    section: dict[str, Any],
    analysis: CanonicalAnalysis,
    mapper_agent,  # noqa: ANN001
) -> dict[str, Any]:
    """Fill a section the template defined but this system has no producer for."""
    declared = section.get("fields") or {}
    empty = {key: NOT_AVAILABLE for key in declared}

    if mapper_agent is None:
        from app.agents.report_mapper import ReportMapperAgent

        mapper_agent = ReportMapperAgent()

    try:
        run = mapper_agent.map_section(
            section.get("key", "section"),
            section,
            {
                "case_id": analysis.case_ref or analysis.case_id,
                "final_status": str(analysis.final_status),
                "manual_review_required": analysis.manual_review_required,
            },
        )
    except Exception as exc:  # noqa: BLE001 - a failed section is not a failed report
        logger.warning(
            "report mapping failed for section %s: %s", section.get("key"), type(exc).__name__
        )
        return empty

    return {**empty, **run.data.content}

"""Report generation and the QA gate.

The order matters: build the canonical analysis, map it deterministically onto
the bank's template, render, then check the rendered payload back against the
analysis. QA runs after generation rather than before, because the thing worth
checking is the artefact that will actually be sent.

A HIGH severity QA error triggers exactly one regeneration. If the same class
of error survives that, the report is stored as QA_FAILED rather than quietly
released — a report a bank cannot trust is worse than a missing one.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.qa_agent import QAAgent, combine_qa, deterministic_qa
from app.config import settings
from app.models.enums import AuditAction, ErrorCode, ReportStatus
from app.models.report import Report
from app.reports.docx import render_docx
from app.reports.generator import build_report, load_template
from app.reports.pdf import render_pdf
from app.rules import load_rule_config
from app.services import audit_service, case_service
from app.storage import Storage, get_storage
from app.workflows.analysis_workflow import build_analysis

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 1


def generate_report(
    db: Session,
    case_id: UUID | str,
    *,
    template_id: str | None = None,
    actor: str = "worker",
    run_agent_qa: bool = True,
) -> Report:
    """Generate, QA and store a report for a case."""
    case = case_service.get_case(db, case_id)
    config = load_rule_config(case.bank_id)
    resolved_template = template_id or config.report_template

    analysis = build_analysis(db, case.id)

    report = Report(
        case_id=case.id,
        bank_id=case.bank_id,
        template_id=resolved_template,
        status=ReportStatus.PENDING,
        analysis_snapshot=analysis.model_dump(mode="json"),
        analysis_version=analysis.versions.analysis_version or settings.ANALYSIS_VERSION,
        model_name=analysis.versions.model,
        prompt_version=analysis.versions.prompt_version,
        rules_version=analysis.versions.rules_version or config.version,
        generated_by=actor,
        overall_status=str(analysis.final_status),
    )
    db.add(report)
    db.flush()

    template = load_template(resolved_template)
    attempt = 0
    payload: dict = {}
    qa_result = None

    while attempt <= MAX_REGENERATIONS:
        attempt += 1
        try:
            payload = build_report(analysis, template_id=resolved_template)
        except Exception as exc:  # noqa: BLE001
            logger.exception("report generation failed for case %s", case.case_ref)
            report.status = ReportStatus.FAILED
            report.error_detail = f"{type(exc).__name__}: {exc}"
            db.add(report)
            db.flush()
            return report

        deterministic_errors = deterministic_qa(analysis, payload, template)
        agent_result = None
        if run_agent_qa:
            try:
                agent_result = QAAgent().review(analysis, payload).data
            except Exception as exc:  # noqa: BLE001 - the deterministic pass still stands
                logger.warning("QA agent unavailable: %s", type(exc).__name__)

        qa_result = combine_qa(deterministic_errors, agent_result)
        if not qa_result.requires_regeneration:
            break

        logger.warning(
            "QA requires regeneration for case %s (attempt %d): %s",
            case.case_ref,
            attempt,
            [error.type for error in qa_result.high_errors()],
        )

    report.report_json = payload
    report.qa_passed = bool(qa_result and qa_result.passed)
    report.qa_errors = [error.model_dump() for error in (qa_result.errors if qa_result else [])]
    report.regenerated_count = attempt - 1
    report.status = (
        ReportStatus.QA_FAILED
        if qa_result and qa_result.requires_regeneration
        else ReportStatus.GENERATED
    )
    if report.status == ReportStatus.QA_FAILED:
        report.error_detail = str(ErrorCode.QA_FAILED)

    _render_and_store(db, report, payload, template)

    db.add(report)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.REPORT_GENERATED,
        case_id=case.id,
        actor=actor,
        entity_type="report",
        entity_id=str(report.id),
        details={
            "template_id": resolved_template,
            "status": report.status,
            "qa_passed": report.qa_passed,
            "qa_errors": len(report.qa_errors or []),
            "regenerated": report.regenerated_count,
        },
        model_name=report.model_name,
        prompt_version=report.prompt_version,
        rules_version=report.rules_version,
    )
    return report


def _render_and_store(db: Session, report: Report, payload: dict, template: dict) -> None:
    """Render both formats. A failure in one must not lose the other."""
    store = get_storage()

    for extension, renderer in (("docx", render_docx), ("pdf", render_pdf)):
        try:
            content = renderer(payload, template)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s rendering failed for report %s", extension, report.id)
            report.error_detail = (
                f"{report.error_detail + '; ' if report.error_detail else ''}"
                f"{extension} rendering failed: {type(exc).__name__}"
            )
            continue

        key = Storage.report_key(str(report.case_id), str(report.id), extension)
        try:
            store.put(key, content, overwrite=True)
        except Exception:  # noqa: BLE001
            logger.exception("could not store the %s report", extension)
            continue

        if extension == "docx":
            report.docx_path = key
        else:
            report.pdf_path = key

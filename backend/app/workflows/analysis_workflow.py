"""Case-level orchestration.

This is the pipeline, written out in one place so the order of operations is
readable rather than emergent. Each step updates the case status, records an
audit entry, and hands a defined structure to the next one.

Two properties are load-bearing. A document that fails does not fail the case.
And the model is consulted only where deterministic work has already run out of
answers: candidates come from rules, the reasoner sees only the ambiguous ones,
and the verifier sees only what matters most.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.discrepancy_reasoner import DiscrepancyReasonerAgent, cap_severity, is_dismissed
from app.agents.evidence_verifier import (
    EvidenceVerifierAgent,
    apply_verification,
    needs_verification,
)
from app.agents.profile_builder import ProfileBuilderAgent, collect_candidates
from app.config import settings
from app.models.applicant import ApplicantProfile
from app.models.case import Case
from app.models.discrepancy import Discrepancy
from app.models.document import Document
from app.models.enums import (
    AuditAction,
    CaseStatus,
    DiscrepancyClassification,
    ErrorCode,
    OverallStatus,
    ReviewDecision,
    RuleResult,
    Severity,
)
from app.models.evidence import Evidence
from app.models.extraction import FieldValue
from app.models.validation import ValidationResult
from app.rules import (
    DocumentView,
    FieldObservation,
    RuleContext,
    RuleEngine,
    decide_status,
    load_rule_config,
    overall_confidence,
)
from app.schemas.applicant import ApplicantProfileSchema
from app.schemas.discrepancy import CandidateDiscrepancy, DiscrepancyOut, EvidenceRef
from app.schemas.report import (
    AnalysisVersions,
    CanonicalAnalysis,
    DocumentSummary,
    MissingDocument,
    ValidationSummary,
)
from app.services import audit_service, case_service
from app.workflows.extraction_workflow import DocumentOutcome, page_texts_for, process_document

logger = logging.getLogger(__name__)


@dataclass
class AnalysisRunResult:
    case_id: str
    status: OverallStatus
    documents_processed: int
    documents_failed: int
    discrepancies: int
    high_severity: int
    llm_calls: int = 0
    models_used: set[str] = dataclass_field(default_factory=set)
    prompt_versions: set[str] = dataclass_field(default_factory=set)


def run_analysis(db: Session, case_id: UUID | str, *, actor: str = "worker") -> AnalysisRunResult:
    """Run the full pipeline for one case."""
    case = case_service.get_case(db, case_id)
    config = load_rule_config(case.bank_id)

    case.analysis_version = settings.ANALYSIS_VERSION
    case.rules_version = config.version
    audit_service.record(
        db,
        action=AuditAction.ANALYSIS_STARTED,
        case_id=case.id,
        actor=actor,
        entity_type="case",
        entity_id=str(case.id),
        details={"bank_id": case.bank_id},
        rules_version=config.version,
    )

    try:
        return _run(db, case, config, actor=actor)
    except Exception as exc:  # noqa: BLE001 - the case must not be left mid-flight
        logger.exception("analysis failed for case %s", case.case_ref)
        case_service.set_status(
            db,
            case,
            CaseStatus.FAILED,
            step="failed",
            error_code=str(ErrorCode.RULE_ENGINE_FAILED),
            error_detail=f"{type(exc).__name__}: {exc}",
            actor=actor,
        )
        audit_service.record(
            db,
            action=AuditAction.ANALYSIS_FAILED,
            case_id=case.id,
            actor=actor,
            details={"error": type(exc).__name__},
        )
        raise


def _run(db: Session, case: Case, config, *, actor: str) -> AnalysisRunResult:  # noqa: ANN001
    models: set[str] = set()
    prompts: set[str] = set()
    llm_calls = 0

    # ------------------------------------------------- documents (steps 1-5)
    case_service.set_status(db, case, CaseStatus.PROCESSING, step="parsing", actor=actor)
    _clear_previous_results(db, case.id)

    documents = list(db.scalars(select(Document).where(Document.case_id == case.id)))
    outcomes: list[DocumentOutcome] = []

    case_service.set_status(db, case, CaseStatus.EXTRACTING, step="extracting", actor=actor)
    for document in documents:
        outcome = process_document(db, document)
        outcomes.append(outcome)
        models |= outcome.models_used
        prompts |= outcome.prompt_versions
        llm_calls += 2  # classification plus extraction

    failed = [o for o in outcomes if not o.ok]
    if documents and len(failed) == len(documents):
        # Nothing was read at all: continuing would produce an empty profile
        # and a report that says everything is missing, which is misleading.
        case_service.set_status(
            db,
            case,
            CaseStatus.FAILED,
            step="failed",
            error_code=str(ErrorCode.EXTRACTION_FAILED),
            error_detail="none of the uploaded documents could be processed",
            actor=actor,
        )
        return AnalysisRunResult(
            case_id=str(case.id),
            status=OverallStatus.REVIEW_REQUIRED,
            documents_processed=0,
            documents_failed=len(failed),
            discrepancies=0,
            high_severity=0,
            llm_calls=llm_calls,
            models_used=models,
            prompt_versions=prompts,
        )

    # ------------------------------------------------------- profile (6)
    case_service.set_status(db, case, CaseStatus.VALIDATING, step="building_profile", actor=actor)
    observations = _observations(db, case.id)
    candidates_by_field = collect_candidates(_extraction_payloads(db, case.id))

    profile_run = ProfileBuilderAgent().build(candidates_by_field)
    profile = profile_run.data
    if profile_run.attempts:
        llm_calls += 1
        models.add(profile_run.model)
        prompts.add(profile_run.prompt_version)

    _persist_profile(db, case, profile, profile_run.model, profile_run.prompt_version)

    # --------------------------------------------------- rule engine (7-8)
    case_service.set_status(db, case, CaseStatus.VALIDATING, step="running_rules", actor=actor)
    context = RuleContext(
        profile=profile,
        documents=_document_views(db, documents),
        observations=observations,
        config=config,
        as_of=date.today(),
    )
    rule_outcomes = RuleEngine(config).run(context)
    _persist_validations(db, case, rule_outcomes, config.version)

    candidates = RuleEngine.candidates(rule_outcomes)

    # ------------------------------------------------------- reasoning (9)
    case_service.set_status(db, case, CaseStatus.VALIDATING, step="reasoning", actor=actor)
    reasoner = DiscrepancyReasonerAgent()
    assessments: dict[int, object] = {}

    for index, candidate in enumerate(candidates):
        if not candidate.needs_reasoning or not config.semantic_escalation:
            continue
        try:
            run = reasoner.assess(candidate)
        except Exception as exc:  # noqa: BLE001 - keep the finding, lose the opinion
            logger.warning("reasoning failed for %s: %s", candidate.type, type(exc).__name__)
            continue
        assessments[index] = run.data
        llm_calls += 1
        models.add(run.model)
        prompts.add(run.prompt_version)

    # ---------------------------------------------------- verification (10)
    case_service.set_status(db, case, CaseStatus.VALIDATING, step="verifying_evidence", actor=actor)
    verifier = EvidenceVerifierAgent()
    page_texts = page_texts_for(db, case.id)
    verifications: dict[int, object] = {}

    for index, candidate in enumerate(candidates):
        assessment = assessments.get(index)
        if not needs_verification(candidate, assessment):
            continue
        try:
            run = verifier.verify(candidate, assessment, page_texts=page_texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verification failed for %s: %s", candidate.type, type(exc).__name__)
            continue
        verifications[index] = run.data
        llm_calls += 1
        models.add(run.model)
        prompts.add(run.prompt_version)

    # ------------------------------------------------- final flags (11-13)
    stored = _persist_discrepancies(
        db, case, candidates, assessments, verifications, config.version, models
    )

    # -------------------------------------------------- analysis + status
    case_service.set_status(db, case, CaseStatus.VALIDATING, step="building_analysis", actor=actor)
    missing = _missing_documents(rule_outcomes)
    findings = [_to_out(discrepancy) for discrepancy in stored]
    decision = decide_status(findings, missing, config)

    case_service.set_status(
        db,
        case,
        CaseStatus.REVIEW_REQUIRED if decision.manual_review_required else CaseStatus.COMPLETED,
        step="finalised",
        actor=actor,
    )
    case.model_name = ", ".join(sorted(models)) if models else None
    db.add(case)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.ANALYSIS_COMPLETED,
        case_id=case.id,
        actor=actor,
        details={
            "status": str(decision.status),
            "findings": len(findings),
            "llm_calls": llm_calls,
            "documents_failed": len(failed),
        },
        model_name=", ".join(sorted(models)) if models else None,
        rules_version=config.version,
    )

    return AnalysisRunResult(
        case_id=str(case.id),
        status=decision.status,
        documents_processed=len(outcomes) - len(failed),
        documents_failed=len(failed),
        discrepancies=len(findings),
        high_severity=sum(1 for f in findings if f.severity == Severity.HIGH),
        llm_calls=llm_calls,
        models_used=models,
        prompt_versions=prompts,
    )


# --------------------------------------------------------------------------
# Building the canonical analysis
# --------------------------------------------------------------------------
def build_analysis(db: Session, case_id: UUID | str) -> CanonicalAnalysis:
    """Assemble the one structure every downstream consumer reads."""
    case = case_service.get_case(db, case_id)
    config = load_rule_config(case.bank_id)

    profile = ApplicantProfileSchema(fields={})
    if case.profile is not None:
        profile = ApplicantProfileSchema.model_validate({"fields": case.profile.fields})

    documents = list(db.scalars(select(Document).where(Document.case_id == case.id)))
    summaries = [_document_summary(document) for document in documents]

    validations = list(
        db.scalars(select(ValidationResult).where(ValidationResult.case_id == case.id))
    )
    discrepancies = list(
        db.scalars(
            select(Discrepancy)
            .where(Discrepancy.case_id == case.id, Discrepancy.suppressed.is_(False))
            .order_by(Discrepancy.code)
        )
    )
    findings = [_to_out(d) for d in discrepancies]
    missing = _missing_from_validations(validations)
    decision = decide_status(findings, missing, config)

    confidences = [
        field.confidence for field in profile.fields.values() if field.is_present
    ]

    return CanonicalAnalysis(
        case_id=str(case.id),
        case_ref=case.case_ref,
        bank_id=case.bank_id,
        applicant=profile,
        documents=summaries,
        validations=[
            ValidationSummary(
                rule_id=v.rule_id,
                rule_category=v.rule_category,
                result=v.result,
                field=v.field,
                severity=v.severity,
                reason=v.reason,
                evidence=v.evidence or [],
            )
            for v in validations
        ],
        discrepancies=findings,
        missing_documents=missing,
        document_quality=[s for s in summaries if s.quality_status != "NO_ISSUE_OBSERVED"],
        final_status=decision.status,
        overall_confidence=overall_confidence(findings, confidences),
        manual_review_required=decision.manual_review_required,
        versions=AnalysisVersions(
            analysis_version=case.analysis_version or settings.ANALYSIS_VERSION,
            model=case.model_name or settings.LLM_MODEL,
            prompt_version=_prompt_versions(discrepancies),
            rules_version=case.rules_version or config.version,
            generated_at=datetime.now(UTC),
        ),
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _clear_previous_results(db: Session, case_id: UUID) -> None:
    """A re-run replaces its own previous output, never appends to it."""
    db.execute(delete(Evidence).where(Evidence.case_id == case_id))
    db.execute(delete(Discrepancy).where(Discrepancy.case_id == case_id))
    db.execute(delete(ValidationResult).where(ValidationResult.case_id == case_id))
    db.execute(delete(FieldValue).where(FieldValue.case_id == case_id))
    db.flush()


def _observations(db: Session, case_id: UUID) -> list[FieldObservation]:
    rows = db.execute(
        select(FieldValue, Document)
        .join(Document, Document.id == FieldValue.document_id)
        .where(FieldValue.case_id == case_id)
    ).all()

    return [
        FieldObservation(
            canonical_field=value.field_name,
            raw_field=value.field_name,
            value=value.original_value or "",
            normalized_value=value.normalized_value,
            confidence=value.confidence,
            document_id=str(value.document_id),
            document_name=document.filename,
            document_type=document.document_type,
            page=value.page_number or 0,
            snippet=value.source_text or "",
            bbox=value.bbox or [],
        )
        for value, document in rows
    ]


def _extraction_payloads(db: Session, case_id: UUID) -> list[dict]:
    """Group field values by document for the profile builder."""
    rows = db.execute(
        select(FieldValue, Document)
        .join(Document, Document.id == FieldValue.document_id)
        .where(FieldValue.case_id == case_id)
    ).all()

    grouped: dict[str, dict] = {}
    for value, document in rows:
        entry = grouped.setdefault(
            str(document.id),
            {
                "document_id": str(document.id),
                "document_name": document.filename,
                "document_type": document.document_type,
                "fields": [],
            },
        )
        entry["fields"].append(
            {
                "canonical_field": value.field_name,
                "value": value.original_value,
                "normalized_value": value.normalized_value,
                "confidence": value.confidence,
                "page": value.page_number or 0,
                "snippet": value.source_text or "",
                "bbox": value.bbox or [],
            }
        )
    return list(grouped.values())


def _document_views(db: Session, documents: list[Document]) -> list[DocumentView]:
    return [
        DocumentView(
            document_id=str(document.id),
            filename=document.filename,
            document_type=document.document_type,
            subtype=document.document_subtype or "",
            page_count=document.page_count,
            sha256=document.sha256,
            is_readable=document.is_readable,
            status=document.status,
            quality_flags=list(document.quality_flags or []),
            classification_confidence=document.classification_confidence,
            ocr_confidences=[
                page.ocr_confidence for page in document.pages if page.ocr_confidence is not None
            ],
            error_code=document.error_code,
        )
        for document in documents
    ]


def _persist_profile(
    db: Session,
    case: Case,
    profile: ApplicantProfileSchema,
    model: str,
    prompt_version: str,
) -> None:
    existing = case.profile
    payload = {name: field.model_dump() for name, field in profile.fields.items()}

    if existing is None:
        db.add(
            ApplicantProfile(
                case_id=case.id,
                fields=payload,
                model_name=model,
                prompt_version=prompt_version,
            )
        )
    else:
        existing.fields = payload
        existing.model_name = model
        existing.prompt_version = prompt_version
        db.add(existing)
    db.flush()


def _persist_validations(
    db: Session, case: Case, outcomes: list, rules_version: str  # noqa: ANN001
) -> None:
    for outcome in outcomes:
        db.add(ValidationResult(case_id=case.id, **outcome.to_row(rules_version)))
    db.flush()


def _persist_discrepancies(
    db: Session,
    case: Case,
    candidates: list[CandidateDiscrepancy],
    assessments: dict,
    verifications: dict,
    rules_version: str,
    models: set[str],
) -> list[Discrepancy]:
    """Write final flags, carrying the whole chain onto each row."""
    stored: list[Discrepancy] = []
    sequence = 0

    for index, candidate in enumerate(candidates):
        assessment = assessments.get(index)
        verification = verifications.get(index)

        severity = str(candidate.severity)
        classification = DiscrepancyClassification.CONFIRMED
        confidence = 0.9 if candidate.deterministic else 0.5
        explanation = candidate.summary
        action = "Verify against the source documents."

        if assessment is not None:
            if is_dismissed(assessment):
                # Dismissed findings are kept, marked suppressed, so the audit
                # chain still shows what was raised and why it was set aside.
                sequence += 1
                stored_row = _build_row(
                    case, candidate, sequence, severity, DiscrepancyClassification.NOT_A_DISCREPANCY,
                    assessment.confidence, assessment.explanation,
                    assessment.recommended_action or "No action required.",
                    rules_version, models, assessment, verification, suppressed=True,
                    suppressed_reason="assessed as not a discrepancy",
                )
                db.add(stored_row)
                continue

            severity = cap_severity(candidate, assessment)
            classification = assessment.classification
            confidence = assessment.confidence
            explanation = assessment.explanation or candidate.summary
            action = assessment.recommended_action or action

        keep, reason = (True, "")
        if verification is not None:
            keep, reason = apply_verification(candidate, assessment, verification)

        sequence += 1
        row = _build_row(
            case, candidate, sequence, severity, classification, confidence, explanation,
            action, rules_version, models, assessment, verification,
            suppressed=not keep, suppressed_reason=reason if not keep else None,
        )
        db.add(row)
        db.flush()

        for ref in candidate.evidence:
            db.add(
                Evidence(
                    case_id=case.id,
                    discrepancy_id=row.id,
                    document_id=UUID(ref.document_id) if _is_uuid(ref.document_id) else None,
                    document_name=ref.document_name,
                    document_type=ref.document_type,
                    page_number=ref.page,
                    field=ref.field,
                    value=ref.value,
                    snippet=ref.snippet,
                    bbox=ref.bbox or None,
                )
            )

        if keep:
            stored.append(row)

    db.flush()
    return stored


def _build_row(
    case: Case,
    candidate: CandidateDiscrepancy,
    sequence: int,
    severity: str,
    classification: str,
    confidence: float,
    explanation: str,
    action: str,
    rules_version: str,
    models: set[str],
    assessment,  # noqa: ANN001
    verification,  # noqa: ANN001
    *,
    suppressed: bool = False,
    suppressed_reason: str | None = None,
) -> Discrepancy:
    return Discrepancy(
        case_id=case.id,
        code=f"D{sequence:03d}",
        type=candidate.type,
        field=candidate.field or None,
        severity=str(severity),
        classification=str(classification),
        confidence=confidence,
        explanation=explanation,
        recommended_action=action,
        origin=candidate.origin,
        rule_id=candidate.rule_id or None,
        comparison_method=candidate.comparison_method or None,
        similarity=candidate.similarity,
        candidate_payload=candidate.model_dump(),
        reasoner_payload=assessment.model_dump() if assessment is not None else None,
        verification_payload=verification.model_dump() if verification is not None else None,
        verified=bool(verification is not None and getattr(verification, "verified", False)),
        suppressed=suppressed,
        suppressed_reason=suppressed_reason,
        model_name=", ".join(sorted(models)) if models else None,
        rules_version=rules_version,
        review_decision=ReviewDecision.PENDING,
    )


def _missing_documents(rule_outcomes: list) -> list[MissingDocument]:  # noqa: ANN001
    return [
        MissingDocument(
            document_type=outcome.field or "UNKNOWN",
            severity=outcome.severity or Severity.HIGH,
            reason=outcome.reason,
            required_by=outcome.rule_id,
        )
        for outcome in rule_outcomes
        if outcome.rule_id == "documents.required_documents" and outcome.result == RuleResult.FAIL
    ]


def _missing_from_validations(validations: list[ValidationResult]) -> list[MissingDocument]:
    return [
        MissingDocument(
            document_type=v.field or "UNKNOWN",
            severity=v.severity or Severity.HIGH,
            reason=v.reason or "",
            required_by=v.rule_id,
        )
        for v in validations
        if v.rule_id == "documents.required_documents" and v.result == RuleResult.FAIL
    ]


def _document_summary(document: Document) -> DocumentSummary:
    return DocumentSummary(
        document_id=str(document.id),
        filename=document.filename,
        document_type=document.document_type,
        subtype=document.document_subtype,
        pages=document.page_count,
        classification_confidence=document.classification_confidence,
        is_readable=document.is_readable,
        status=document.status,
        quality_status=document.quality_status or "NO_ISSUE_OBSERVED",
        quality_flags=list(document.quality_flags or []),
        quality_notes=document.quality_notes,
        sha256=document.sha256,
        uploaded_at=document.created_at,
        error_code=document.error_code,
    )


def _to_out(discrepancy: Discrepancy) -> DiscrepancyOut:
    return DiscrepancyOut(
        id=str(discrepancy.id),
        code=discrepancy.code,
        type=discrepancy.type,
        field=discrepancy.field,
        severity=discrepancy.severity,
        classification=discrepancy.classification,
        confidence=discrepancy.confidence,
        explanation=discrepancy.explanation,
        recommended_action=discrepancy.recommended_action,
        origin=discrepancy.origin,
        rule_id=discrepancy.rule_id,
        verified=discrepancy.verified,
        review_decision=discrepancy.review_decision,
        evidence=[
            EvidenceRef(
                document_id=str(item.document_id) if item.document_id else "",
                document_name=item.document_name or "",
                document_type=item.document_type or "",
                page=item.page_number or 0,
                field=item.field or "",
                value=item.value or "",
                snippet=item.snippet or "",
                bbox=item.bbox or [],
            )
            for item in discrepancy.evidence
        ],
    )


def _prompt_versions(discrepancies: list[Discrepancy]) -> str:
    versions = sorted({d.prompt_version for d in discrepancies if d.prompt_version})
    return ", ".join(versions)


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True

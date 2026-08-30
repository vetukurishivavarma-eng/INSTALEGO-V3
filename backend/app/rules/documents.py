"""Document-level rules: what was supplied, and whether it can be read.

The required-document check is the clearest example of work a model should
never do. It is set arithmetic over classified types against a configured list,
it has one correct answer, and it is the check a bank is most likely to audit.

Nothing here concludes that a document is fraudulent. An unreadable scan, a
duplicate upload and a document whose type could not be established are all
data-quality findings, and they are worded that way.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from app.models.enums import DocumentType, RuleResult, Severity
from app.rules.registry import RuleContext, RuleOutcome, rule
from app.schemas.discrepancy import CandidateDiscrepancy, EvidenceRef

LOW_OCR_THRESHOLD = 0.60


def _document_evidence(document) -> EvidenceRef:  # noqa: ANN001
    return EvidenceRef(
        document_id=document.document_id,
        document_name=document.filename,
        document_type=document.document_type,
        page=1,
        value=document.document_type,
    )


def satisfied_requirements(context: RuleContext) -> dict[str, list[str]]:
    """Which requirement each uploaded document satisfies."""
    groups = context.config.document_type_groups
    satisfied: dict[str, list[str]] = defaultdict(list)

    for requirement in context.config.required_documents:
        accepted = set(groups.get(requirement, [requirement]))
        for document in context.documents:
            if document.document_type in accepted and document.document_type != DocumentType.UNKNOWN:
                satisfied[requirement].append(document.document_id)
    return dict(satisfied)


@rule("documents", "required_documents")
def required_documents(context: RuleContext) -> Iterable[RuleOutcome]:
    rule_id = "documents.required_documents"
    severity = context.config.severity("documents", "required_documents", Severity.HIGH)
    required = context.config.required_documents

    if not required:
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.NOT_APPLICABLE,
            reason="the bank configuration lists no required documents",
        )
        return

    satisfied = satisfied_requirements(context)
    groups = context.config.document_type_groups

    for requirement in required:
        if satisfied.get(requirement):
            yield RuleOutcome(
                rule_id=rule_id,
                category="documents",
                result=RuleResult.PASS,
                field=requirement,
                reason=f"{requirement} is satisfied by {len(satisfied[requirement])} document(s)",
            )
            continue

        accepted = groups.get(requirement, [requirement])
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.FAIL,
            field=requirement,
            severity=severity,
            reason=f"no document of type {requirement} was supplied",
            candidate=CandidateDiscrepancy(
                type="MISSING_REQUIRED_DOCUMENT",
                field=requirement,
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="set",
                summary=(
                    f"{requirement} is required by {context.config.bank_id} but nothing "
                    f"satisfying it was supplied. Accepted types: {', '.join(accepted)}."
                ),
                values=[requirement],
                evidence=[],
                # Absence is a fact about the upload, not a matter of opinion.
                needs_reasoning=False,
                deterministic=True,
            ),
        )


@rule("documents", "duplicate_document")
def duplicate_document(context: RuleContext) -> Iterable[RuleOutcome]:
    """The same file uploaded twice, detected by content hash."""
    rule_id = "documents.duplicate_document"
    severity = context.config.severity("documents", "duplicate_document", Severity.LOW)

    by_hash: dict[str, list] = defaultdict(list)
    for document in context.documents:
        if document.sha256:
            by_hash[document.sha256].append(document)

    duplicates = {digest: docs for digest, docs in by_hash.items() if len(docs) > 1}
    if not duplicates:
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.PASS,
            reason="no duplicate uploads were found",
        )
        return

    for digest, docs in duplicates.items():
        names = ", ".join(d.filename for d in docs)
        evidence = [_document_evidence(d) for d in docs]
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.REVIEW,
            severity=severity,
            reason=f"{len(docs)} uploads share the same content: {names}",
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="DUPLICATE_DOCUMENT",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="hash",
                summary=(
                    f"These uploads are byte-identical (SHA-256 {digest[:12]}): {names}. "
                    "This is usually an accidental re-upload."
                ),
                values=[d.filename for d in docs],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )


@rule("documents", "unreadable_document")
def unreadable_document(context: RuleContext) -> Iterable[RuleOutcome]:
    rule_id = "documents.unreadable_document"
    severity = context.config.severity("documents", "unreadable_document", Severity.MEDIUM)
    flagged = False

    for document in context.documents:
        if document.is_readable and not document.error_code:
            continue
        flagged = True
        evidence = [_document_evidence(document)]
        detail = document.error_code or "the content could not be read"
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.REVIEW,
            field=document.filename,
            severity=severity,
            reason=f"{document.filename}: {detail}",
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="UNREADABLE_DOCUMENT",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="quality",
                summary=(
                    f"{document.filename} could not be read ({detail}), so nothing was "
                    "extracted from it. A legible copy is needed before this file can be "
                    "assessed."
                ),
                values=[document.filename],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if not flagged:
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.PASS,
            reason="every uploaded document was readable",
        )


@rule("documents", "low_ocr_confidence")
def low_ocr_confidence(context: RuleContext) -> Iterable[RuleOutcome]:
    """Pages transcribed with low confidence taint anything read from them."""
    rule_id = "documents.low_ocr_confidence"
    severity = context.config.severity("documents", "low_ocr_confidence", Severity.LOW)
    flagged = False

    for document in context.documents:
        low = [c for c in document.ocr_confidences if c < LOW_OCR_THRESHOLD]
        if not low:
            continue
        flagged = True
        evidence = [_document_evidence(document)]
        average = sum(low) / len(low)
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.REVIEW,
            field=document.filename,
            severity=severity,
            reason=(
                f"{document.filename}: {len(low)} page(s) transcribed at an average confidence "
                f"of {average:.2f}"
            ),
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="LOW_OCR_CONFIDENCE",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="quality",
                summary=(
                    f"{len(low)} page(s) of {document.filename} were transcribed at low "
                    f"confidence (average {average:.2f}). Values read from those pages should "
                    "be checked against the original."
                ),
                values=[document.filename],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if not flagged:
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.PASS,
            reason="no page was transcribed at low confidence",
        )


@rule("documents", "unclassified_document")
def unclassified_document(context: RuleContext) -> Iterable[RuleOutcome]:
    """A document whose type could not be established cannot satisfy anything."""
    rule_id = "documents.unclassified_document"
    severity = context.config.severity("documents", "unclassified_document", Severity.MEDIUM)
    unknown = [d for d in context.documents if d.document_type == DocumentType.UNKNOWN]

    if not unknown:
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.PASS,
            reason="every document was classified",
        )
        return

    for document in unknown:
        evidence = [_document_evidence(document)]
        yield RuleOutcome(
            rule_id=rule_id,
            category="documents",
            result=RuleResult.REVIEW,
            field=document.filename,
            severity=severity,
            reason=f"{document.filename}: the document type could not be established",
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="UNCLASSIFIED_DOCUMENT",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="classification",
                summary=(
                    f"{document.filename} could not be classified, so it does not count "
                    "towards any document requirement and no targeted extraction was run "
                    "against it."
                ),
                values=[document.filename],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

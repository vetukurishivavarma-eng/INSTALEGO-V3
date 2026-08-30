"""Identity rules: is every document about the same person.

Each rule compares one canonical field across the documents that assert it,
using the comparison engine rather than string equality. That distinction is
the whole design: RAVI KUMAR against Ravi Kumar is a PASS, Ravi Kumar against
Ravi K Kumar is a PASS, and Ravi Kumar against Sunita Sharma is a FAIL.

Where a comparison declines to decide, the rule returns REVIEW and attaches a
candidate marked for the reasoning agent. It never guesses in either direction.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.comparison import ComparisonVerdict, FuzzyThresholds, compare_field
from app.models.enums import RuleResult, Severity
from app.rules.registry import RuleContext, RuleOutcome, rule
from app.schemas.discrepancy import CandidateDiscrepancy
from app.utils.identifiers import check_aadhaar, check_pan

# Canonical field -> the discrepancy type raised when it disagrees.
DISCREPANCY_TYPES = {
    "name": "NAME_MISMATCH",
    "date_of_birth": "DOB_MISMATCH",
    "pan": "PAN_MISMATCH",
    "aadhaar": "AADHAAR_MISMATCH",
    "passport": "PASSPORT_MISMATCH",
    "driving_license": "DRIVING_LICENSE_MISMATCH",
    "father_name": "FATHER_NAME_MISMATCH",
    "current_address": "ADDRESS_MISMATCH",
    "phone": "PHONE_MISMATCH",
    "email": "EMAIL_MISMATCH",
}


def _thresholds(context: RuleContext) -> FuzzyThresholds:
    configured = context.config.thresholds
    return FuzzyThresholds(
        name_equal=float(configured.get("name_equal", 0.92)),
        name_different=float(configured.get("name_different", 0.70)),
        address_equal=float(configured.get("address_equal", 0.90)),
        address_different=float(configured.get("address_different", 0.55)),
        organisation_equal=float(configured.get("organisation_equal", 0.88)),
        organisation_different=float(configured.get("organisation_different", 0.60)),
    )


def _cross_document_field(
    context: RuleContext,
    *,
    category: str,
    rule_name: str,
    canonical_field: str,
) -> Iterable[RuleOutcome]:
    """Compare every pair of documents that state this field."""
    rule_id = f"{category}.{rule_name}"
    observations = context.values_for(canonical_field)
    settings = context.config.rule(category, rule_name)
    severity = str(settings.get("severity", Severity.MEDIUM))
    escalate = bool(settings.get("escalate_undetermined", False))

    if len(observations) < 2:
        yield RuleOutcome(
            rule_id=rule_id,
            category=category,
            result=RuleResult.NOT_APPLICABLE,
            field=canonical_field,
            reason=(
                "only one document states this field; there is nothing to compare"
                if observations
                else "no document states this field"
            ),
            evidence=[o.as_evidence() for o in observations],
        )
        return

    reference = observations[0]
    compared_any = False

    for other in observations[1:]:
        outcome = compare_field(
            canonical_field,
            reference.value,
            other.value,
            thresholds=_thresholds(context),
        )
        if outcome.verdict == ComparisonVerdict.NOT_COMPARABLE:
            continue
        compared_any = True

        if outcome.is_equal:
            continue

        evidence = [reference.as_evidence(), other.as_evidence()]
        label = canonical_field.replace("_", " ")

        if outcome.is_different:
            yield RuleOutcome(
                rule_id=rule_id,
                category=category,
                result=RuleResult.FAIL,
                field=canonical_field,
                severity=severity,
                reason=(
                    f"{label} differs between {reference.document_name} and "
                    f"{other.document_name}: {outcome.reason}"
                ),
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type=DISCREPANCY_TYPES.get(canonical_field, "FIELD_MISMATCH"),
                    field=canonical_field,
                    severity=severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method=outcome.method,
                    similarity=outcome.similarity,
                    summary=(
                        f"{reference.document_name} gives {label} as {reference.value}; "
                        f"{other.document_name} gives {other.value}."
                    ),
                    values=[reference.value, other.value],
                    evidence=evidence,
                    # A clear identifier mismatch needs no model to interpret
                    # it, so the call is spent only where judgement is needed.
                    needs_reasoning=outcome.method != "exact",
                    deterministic=outcome.method == "exact",
                ),
            )
        elif escalate:
            yield RuleOutcome(
                rule_id=rule_id,
                category=category,
                result=RuleResult.REVIEW,
                field=canonical_field,
                severity=severity,
                reason=f"{label} could not be settled deterministically: {outcome.reason}",
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type=DISCREPANCY_TYPES.get(canonical_field, "FIELD_MISMATCH"),
                    field=canonical_field,
                    severity=severity,
                    rule_id=rule_id,
                    origin="COMPARISON",
                    comparison_method=outcome.method,
                    similarity=outcome.similarity,
                    summary=(
                        f"{reference.document_name} gives {label} as {reference.value}; "
                        f"{other.document_name} gives {other.value}. "
                        "Deterministic comparison could not decide whether these are the same."
                    ),
                    values=[reference.value, other.value],
                    evidence=evidence,
                    needs_reasoning=True,
                ),
            )

    if compared_any:
        yield RuleOutcome(
            rule_id=rule_id,
            category=category,
            result=RuleResult.PASS,
            field=canonical_field,
            reason=f"{canonical_field.replace('_', ' ')} is consistent across {len(observations)} documents",
            evidence=[o.as_evidence() for o in observations],
        )


@rule("identity", "name_match")
def name_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="name_match", canonical_field="name"
    )


@rule("identity", "dob_match")
def dob_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="dob_match", canonical_field="date_of_birth"
    )


@rule("identity", "pan_match")
def pan_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="pan_match", canonical_field="pan"
    )


@rule("identity", "aadhaar_match")
def aadhaar_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="aadhaar_match", canonical_field="aadhaar"
    )


@rule("identity", "passport_match")
def passport_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="passport_match", canonical_field="passport"
    )


@rule("identity", "driving_license_match")
def driving_license_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context,
        category="identity",
        rule_name="driving_license_match",
        canonical_field="driving_license",
    )


@rule("identity", "father_name_match")
def father_name_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="father_name_match", canonical_field="father_name"
    )


@rule("identity", "address_match")
def address_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="address_match", canonical_field="current_address"
    )


@rule("identity", "phone_match")
def phone_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="phone_match", canonical_field="phone"
    )


@rule("identity", "email_match")
def email_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _cross_document_field(
        context, category="identity", rule_name="email_match", canonical_field="email"
    )


@rule("identity", "pan_format")
def pan_format(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _identifier_format(
        context, rule_name="pan_format", canonical_field="pan", checker=check_pan,
        finding_type="PAN_FORMAT_INVALID",
    )


@rule("identity", "aadhaar_checksum")
def aadhaar_checksum(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _identifier_format(
        context, rule_name="aadhaar_checksum", canonical_field="aadhaar", checker=check_aadhaar,
        finding_type="AADHAAR_CHECKSUM_INVALID",
    )


def _identifier_format(
    context: RuleContext,
    *,
    rule_name: str,
    canonical_field: str,
    checker,  # noqa: ANN001
    finding_type: str,
) -> Iterable[RuleOutcome]:
    """Structural validity of an identifier as printed on a document.

    A failure here is a data-quality finding. The overwhelmingly likely cause
    is a misread character, so the wording stays neutral and the outcome is
    REVIEW rather than FAIL.
    """
    rule_id = f"identity.{rule_name}"
    observations = context.values_for(canonical_field)
    severity = context.config.severity("identity", rule_name, Severity.MEDIUM)

    if not observations:
        yield RuleOutcome(
            rule_id=rule_id,
            category="identity",
            result=RuleResult.NOT_APPLICABLE,
            field=canonical_field,
            reason="no document states this identifier",
        )
        return

    for observation in observations:
        check = checker(observation.value)
        if check.valid_format:
            yield RuleOutcome(
                rule_id=rule_id,
                category="identity",
                result=RuleResult.PASS,
                field=canonical_field,
                reason=f"{observation.document_name}: {check.reason}",
                evidence=[observation.as_evidence()],
            )
            continue

        evidence = [observation.as_evidence()]
        yield RuleOutcome(
            rule_id=rule_id,
            category="identity",
            result=RuleResult.REVIEW,
            field=canonical_field,
            severity=severity,
            reason=f"{observation.document_name}: {check.reason}",
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type=finding_type,
                field=canonical_field,
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="format",
                summary=(
                    f"The {canonical_field.replace('_', ' ')} read from "
                    f"{observation.document_name} is {observation.value}, which {check.reason}. "
                    "This is commonly a scanning or transcription artefact."
                ),
                values=[observation.value],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

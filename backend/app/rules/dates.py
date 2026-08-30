"""Date rules: expiry, ordering and plausibility.

Expiry is the one check here that can stop a file on its own, so it is also the
one that most needs to refuse to guess: an expiry date that cannot be parsed
produces REVIEW, never an assertion that a document has lapsed.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.enums import RuleResult, Severity
from app.rules.registry import RuleContext, RuleOutcome, rule
from app.schemas.discrepancy import CandidateDiscrepancy
from app.utils.dates import age_on, in_order, is_expired, parse_date


@rule("dates", "document_expiry")
def document_expiry(context: RuleContext) -> Iterable[RuleOutcome]:
    """Any document carrying an expiry date that has passed."""
    rule_id = "dates.document_expiry"
    settings = context.config.rule("dates", "document_expiry")
    critical = set(settings.get("critical_types") or [])
    critical_severity = str(settings.get("severity", Severity.HIGH))
    other_severity = str(settings.get("non_critical_severity", Severity.MEDIUM))

    observations = context.values_for("date_of_expiry")
    if not observations:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.NOT_APPLICABLE,
            reason="no document states an expiry date",
        )
        return

    for observation in observations:
        expired, reason = is_expired(observation.value, as_of=context.as_of)
        evidence = [observation.as_evidence()]
        severity = critical_severity if observation.document_type in critical else other_severity

        if expired is None:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.REVIEW,
                field="date_of_expiry",
                severity=other_severity,
                reason=f"{observation.document_name}: {reason}",
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type="EXPIRY_UNREADABLE",
                    field="date_of_expiry",
                    severity=other_severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method="date",
                    summary=(
                        f"The expiry date on {observation.document_name} reads "
                        f"{observation.value!r} and could not be interpreted."
                    ),
                    values=[observation.value],
                    evidence=evidence,
                    needs_reasoning=False,
                    deterministic=True,
                ),
            )
        elif expired:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.FAIL,
                field="date_of_expiry",
                severity=severity,
                reason=f"{observation.document_name} {reason}",
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type="EXPIRED_DOCUMENT",
                    field="date_of_expiry",
                    severity=severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method="date",
                    summary=(
                        f"{observation.document_name} ({observation.document_type}) "
                        f"{reason}, before the review date {context.as_of.isoformat()}."
                    ),
                    values=[observation.value],
                    evidence=evidence,
                    needs_reasoning=False,
                    deterministic=True,
                ),
            )
        else:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.PASS,
                field="date_of_expiry",
                reason=f"{observation.document_name} is {reason}",
                evidence=evidence,
            )


@rule("dates", "dob_plausibility")
def dob_plausibility(context: RuleContext) -> Iterable[RuleOutcome]:
    """An applicant too young to contract, or implausibly old, is a data error."""
    rule_id = "dates.dob_plausibility"
    settings = context.config.rule("dates", "dob_plausibility")
    minimum = int(settings.get("min_age", 18))
    maximum = int(settings.get("max_age", 100))
    severity = str(settings.get("severity", Severity.MEDIUM))

    field = context.profile.get("date_of_birth")
    if field is None or not field.is_present or not field.value:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.NOT_APPLICABLE,
            field="date_of_birth",
            reason="no date of birth was extracted",
        )
        return

    age = age_on(field.value, as_of=context.as_of)
    evidence = [o.as_evidence() for o in context.values_for("date_of_birth")]

    if age is None:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.REVIEW,
            field="date_of_birth",
            severity=severity,
            reason=f"the date of birth {field.value!r} could not be interpreted",
            evidence=evidence,
        )
        return

    if age < minimum or age > maximum:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.FAIL,
            field="date_of_birth",
            severity=severity,
            reason=f"the applicant would be {age}, outside the accepted range {minimum}-{maximum}",
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="DOB_IMPLAUSIBLE",
                field="date_of_birth",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="date",
                summary=(
                    f"The date of birth {field.value} implies an age of {age} at the review "
                    f"date, outside the accepted range of {minimum} to {maximum}."
                ),
                values=[field.value],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )
        return

    yield RuleOutcome(
        rule_id=rule_id,
        category="dates",
        result=RuleResult.PASS,
        field="date_of_birth",
        reason=f"the applicant is {age}, within the accepted range",
        evidence=evidence,
    )


@rule("dates", "date_ordering")
def date_ordering(context: RuleContext) -> Iterable[RuleOutcome]:
    """Issue must precede expiry, and birth must precede issue."""
    rule_id = "dates.date_ordering"
    severity = context.config.severity("dates", "date_ordering", Severity.MEDIUM)
    checked = 0

    for document in context.documents:
        issue = context.observation_for_document(document.document_id, "date_of_issue")
        expiry = context.observation_for_document(document.document_id, "date_of_expiry")
        if not issue or not expiry:
            continue

        checked += 1
        ordered, reason = in_order(issue.value, expiry.value)
        evidence = [issue.as_evidence(), expiry.as_evidence()]

        if ordered is False:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.FAIL,
                field="date_of_issue",
                severity=severity,
                reason=f"{document.filename}: issue date is after expiry date ({reason})",
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type="DATE_ORDER_INVALID",
                    field="date_of_issue",
                    severity=severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method="date",
                    summary=(
                        f"On {document.filename} the issue date {issue.value} falls after the "
                        f"expiry date {expiry.value}."
                    ),
                    values=[issue.value, expiry.value],
                    evidence=evidence,
                    needs_reasoning=False,
                    deterministic=True,
                ),
            )
        elif ordered is None:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.REVIEW,
                field="date_of_issue",
                severity=severity,
                reason=f"{document.filename}: {reason}",
                evidence=evidence,
            )
        else:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.PASS,
                field="date_of_issue",
                reason=f"{document.filename}: {reason}",
                evidence=evidence,
            )

    if not checked:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.NOT_APPLICABLE,
            reason="no document supplied both an issue and an expiry date",
        )


@rule("dates", "document_date_future")
def document_date_future(context: RuleContext) -> Iterable[RuleOutcome]:
    """A document dated in the future is either a typo or worth a look."""
    rule_id = "dates.document_date_future"
    severity = context.config.severity("dates", "document_date_future", Severity.MEDIUM)
    observations = context.values_for("document_date")

    if not observations:
        yield RuleOutcome(
            rule_id=rule_id,
            category="dates",
            result=RuleResult.NOT_APPLICABLE,
            reason="no document states its own date",
        )
        return

    for observation in observations:
        parsed = parse_date(observation.value)
        if not parsed.ok:
            continue
        evidence = [observation.as_evidence()]
        if parsed.value > context.as_of:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.FAIL,
                field="document_date",
                severity=severity,
                reason=f"{observation.document_name} is dated {parsed.iso}, in the future",
                evidence=evidence,
                candidate=CandidateDiscrepancy(
                    type="DOCUMENT_DATE_IN_FUTURE",
                    field="document_date",
                    severity=severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method="date",
                    summary=(
                        f"{observation.document_name} carries the date {parsed.iso}, which is "
                        f"after the review date {context.as_of.isoformat()}."
                    ),
                    values=[observation.value],
                    evidence=evidence,
                    needs_reasoning=False,
                    deterministic=True,
                ),
            )
        else:
            yield RuleOutcome(
                rule_id=rule_id,
                category="dates",
                result=RuleResult.PASS,
                field="document_date",
                reason=f"{observation.document_name} is dated {parsed.iso}",
                evidence=evidence,
            )

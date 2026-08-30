"""Financial rules: do the numbers agree with each other.

Tolerances matter more here than anywhere else. A salary slip and an ITR will
never agree to the rupee, so comparing them at zero tolerance produces a
finding on every file and teaches reviewers to ignore the system. The loan
amount is the opposite case: the figure on the application and the figure in
the sanction are meant to be identical, so it is compared exactly.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.comparison.exact import compare_amount
from app.models.enums import RuleResult, Severity
from app.rules.registry import RuleContext, RuleOutcome, rule
from app.schemas.discrepancy import CandidateDiscrepancy
from app.utils.numbers import parse_amount


def _amount_agreement(
    context: RuleContext,
    *,
    rule_name: str,
    canonical_field: str,
    finding_type: str,
) -> Iterable[RuleOutcome]:
    rule_id = f"financial.{rule_name}"
    settings = context.config.rule("financial", rule_name)
    severity = str(settings.get("severity", Severity.MEDIUM))
    tolerance = float(settings.get("tolerance_pct", 0.0))

    observations = context.values_for(canonical_field)
    if len(observations) < 2:
        yield RuleOutcome(
            rule_id=rule_id,
            category="financial",
            result=RuleResult.NOT_APPLICABLE,
            field=canonical_field,
            reason=(
                "only one document states this amount"
                if observations
                else "no document states this amount"
            ),
            evidence=[o.as_evidence() for o in observations],
        )
        return

    reference = observations[0]
    compared = False

    for other in observations[1:]:
        outcome = compare_amount(reference.value, other.value, tolerance_pct=tolerance)
        if outcome.verdict == "NOT_COMPARABLE":
            continue
        compared = True
        if outcome.is_equal:
            continue

        evidence = [reference.as_evidence(), other.as_evidence()]
        label = canonical_field.replace("_", " ")
        yield RuleOutcome(
            rule_id=rule_id,
            category="financial",
            result=RuleResult.FAIL,
            field=canonical_field,
            severity=severity,
            reason=(
                f"{label} differs between {reference.document_name} and "
                f"{other.document_name}: {outcome.reason}"
            ),
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type=finding_type,
                field=canonical_field,
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="exact",
                summary=(
                    f"{reference.document_name} states {label} as {reference.value}; "
                    f"{other.document_name} states {other.value}."
                    + (f" A tolerance of {tolerance}% was applied." if tolerance else "")
                ),
                values=[reference.value, other.value],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if compared:
        yield RuleOutcome(
            rule_id=rule_id,
            category="financial",
            result=RuleResult.PASS,
            field=canonical_field,
            reason=f"{canonical_field.replace('_', ' ')} is consistent across documents",
            evidence=[o.as_evidence() for o in observations],
        )


@rule("financial", "loan_amount_match")
def loan_amount_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _amount_agreement(
        context,
        rule_name="loan_amount_match",
        canonical_field="loan_amount",
        finding_type="LOAN_AMOUNT_MISMATCH",
    )


@rule("financial", "income_match")
def income_match(context: RuleContext) -> Iterable[RuleOutcome]:
    yield from _amount_agreement(
        context,
        rule_name="income_match",
        canonical_field="income",
        finding_type="INCOME_MISMATCH",
    )


@rule("financial", "bank_account_match")
def bank_account_match(context: RuleContext) -> Iterable[RuleOutcome]:
    """Account numbers are compared as strings; leading zeros are significant."""
    from app.rules.identity import _cross_document_field

    yield from _cross_document_field(
        context,
        category="financial",
        rule_name="bank_account_match",
        canonical_field="bank_account",
    )


@rule("financial", "salary_arithmetic")
def salary_arithmetic(context: RuleContext) -> Iterable[RuleOutcome]:
    """Gross minus deductions should equal net on the same payslip.

    Checked per document rather than across the case: two payslips from
    different months are supposed to differ, and comparing them would
    manufacture a finding out of an ordinary pay rise.
    """
    rule_id = "financial.salary_arithmetic"
    settings = context.config.rule("financial", "salary_arithmetic")
    severity = str(settings.get("severity", Severity.MEDIUM))
    tolerance = float(settings.get("tolerance_abs", 1.0))
    checked = 0

    for document in context.documents:
        gross = context.observation_for_document(document.document_id, "income")
        net = context.observation_for_document(document.document_id, "net_salary")
        deductions = context.observation_for_document(document.document_id, "deductions")
        if not gross or not net or not deductions:
            continue

        gross_value = parse_amount(gross.value)
        net_value = parse_amount(net.value)
        deduction_value = parse_amount(deductions.value)
        if gross_value is None or net_value is None or deduction_value is None:
            continue

        checked += 1
        expected = gross_value - deduction_value
        difference = abs(expected - net_value)
        evidence = [gross.as_evidence(), deductions.as_evidence(), net.as_evidence()]

        if difference <= tolerance:
            yield RuleOutcome(
                rule_id=rule_id,
                category="financial",
                result=RuleResult.PASS,
                field="net_salary",
                reason=f"{document.filename}: gross less deductions equals net",
                evidence=evidence,
            )
            continue

        yield RuleOutcome(
            rule_id=rule_id,
            category="financial",
            result=RuleResult.FAIL,
            field="net_salary",
            severity=severity,
            reason=(
                f"{document.filename}: gross {gross_value} less deductions {deduction_value} "
                f"is {expected}, but net is stated as {net_value}"
            ),
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="SALARY_ARITHMETIC_MISMATCH",
                field="net_salary",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="arithmetic",
                summary=(
                    f"On {document.filename}, gross salary {gross.value} minus deductions "
                    f"{deductions.value} comes to {expected}, while the stated net salary is "
                    f"{net.value}."
                ),
                values=[str(expected), net.value],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if not checked:
        yield RuleOutcome(
            rule_id=rule_id,
            category="financial",
            result=RuleResult.NOT_APPLICABLE,
            reason="no document supplied gross, deductions and net together",
        )

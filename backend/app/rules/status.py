"""The deterministic status policy.

The final status of a case is arithmetic over the findings, driven by the
bank's configuration. No model contributes to it. A reviewer asking "why is
this HIGH_RISK" gets a rule and a count, not a sentence a model wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import DiscrepancyClassification, OverallStatus, ReviewDecision, Severity
from app.rules.registry import RuleConfig
from app.schemas.discrepancy import DiscrepancyOut
from app.schemas.report import MissingDocument


@dataclass
class StatusDecision:
    status: OverallStatus
    manual_review_required: bool
    reasons: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def decide_status(
    discrepancies: list[DiscrepancyOut],
    missing_documents: list[MissingDocument],
    config: RuleConfig,
) -> StatusDecision:
    """Work out the overall status from the findings that remain open."""
    policy = config.status_policy
    open_findings = [
        d for d in discrepancies if d.review_decision in {ReviewDecision.PENDING, ReviewDecision.NEEDS_INFO}
    ]

    high = [d for d in open_findings if d.severity == Severity.HIGH]
    medium = [d for d in open_findings if d.severity == Severity.MEDIUM]
    low = [d for d in open_findings if d.severity == Severity.LOW]
    verified_high = [d for d in high if d.verified]

    counts = {
        "HIGH": len(high),
        "MEDIUM": len(medium),
        "LOW": len(low),
        "MISSING_DOCUMENTS": len(missing_documents),
    }
    reasons: list[str] = []

    if verified_high and policy.get("high_risk_on_verified_high", True):
        reasons.append(
            f"{len(verified_high)} HIGH severity finding(s) survived evidence verification"
        )
        return StatusDecision(
            status=OverallStatus.HIGH_RISK,
            manual_review_required=True,
            reasons=reasons,
            counts=counts,
        )

    if high and policy.get("review_required_on_high", True):
        reasons.append(f"{len(high)} HIGH severity finding(s) are open")
        return StatusDecision(
            status=OverallStatus.REVIEW_REQUIRED,
            manual_review_required=True,
            reasons=reasons,
            counts=counts,
        )

    if missing_documents and policy.get("review_required_on_missing_documents", True):
        reasons.append(f"{len(missing_documents)} required document(s) are missing")
        return StatusDecision(
            status=OverallStatus.REVIEW_REQUIRED,
            manual_review_required=True,
            reasons=reasons,
            counts=counts,
        )

    if medium and policy.get("review_required_on_medium", True):
        reasons.append(f"{len(medium)} MEDIUM severity finding(s) are open")
        return StatusDecision(
            status=OverallStatus.REVIEW_REQUIRED,
            manual_review_required=True,
            reasons=reasons,
            counts=counts,
        )

    # Findings the model could not settle are never treated as clear, whatever
    # their severity: an unresolved question is precisely what review is for.
    uncertain = [
        d for d in open_findings if d.classification == DiscrepancyClassification.UNCERTAIN
    ]
    if uncertain and policy.get("clear_requires_no_open_findings", True):
        reasons.append(f"{len(uncertain)} finding(s) could not be resolved automatically")
        return StatusDecision(
            status=OverallStatus.REVIEW_REQUIRED,
            manual_review_required=True,
            reasons=reasons,
            counts=counts,
        )

    if low:
        reasons.append(f"{len(low)} LOW severity observation(s) recorded; none are material")
    else:
        reasons.append("no discrepancies were found and all required documents were supplied")

    return StatusDecision(
        status=OverallStatus.CLEAR,
        manual_review_required=False,
        reasons=reasons,
        counts=counts,
    )


def overall_confidence(discrepancies: list[DiscrepancyOut], profile_confidences: list[float]) -> float:
    """A blunt, explainable confidence: how well the extraction went.

    Deliberately not a probability of anything. It reports the average
    confidence of the values the analysis rests on, reduced when findings were
    left unresolved, and it is labelled as such in the report.
    """
    base = sum(profile_confidences) / len(profile_confidences) if profile_confidences else 0.0
    unresolved = sum(
        1 for d in discrepancies if d.classification == DiscrepancyClassification.UNCERTAIN
    )
    penalty = min(0.3, 0.05 * unresolved)
    return round(max(0.0, base - penalty), 3)

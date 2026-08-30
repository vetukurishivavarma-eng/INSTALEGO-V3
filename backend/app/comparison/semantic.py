"""Semantic comparison: the escalation path, not a default.

Reached only when exact and fuzzy comparison both decline to decide. The values
are packaged as a candidate discrepancy and handed to the reasoning agent, so
the escalation costs the one model call the pipeline was already going to spend
on an ambiguous pair rather than a second one.

The agent's answer is translated back into a comparison verdict. It is allowed
to say "these are the same thing written differently" and it is allowed to say
"these genuinely differ", but if it is unsure the pair stays undetermined and
goes to a human.
"""

from __future__ import annotations

import logging

from app.comparison.base import ComparisonOutcome, different, equal, undetermined
from app.models.enums import DiscrepancyClassification, Severity
from app.schemas.discrepancy import CandidateDiscrepancy, EvidenceRef

logger = logging.getLogger(__name__)


def semantic_compare(
    field_name: str,
    left: str | None,
    right: str | None,
    *,
    evidence: list[EvidenceRef] | None = None,
    agent=None,  # noqa: ANN001 - injected; avoids importing agents at module load
    summary: str = "",
) -> ComparisonOutcome:
    """Ask the reasoning agent whether two values mean the same thing."""
    left_text, right_text = str(left or ""), str(right or "")

    if agent is None:
        from app.agents.discrepancy_reasoner import DiscrepancyReasonerAgent

        agent = DiscrepancyReasonerAgent()

    candidate = CandidateDiscrepancy(
        type=f"{field_name.upper()}_EQUIVALENCE",
        field=field_name,
        severity=Severity.MEDIUM,
        origin="COMPARISON",
        comparison_method="semantic",
        summary=summary
        or f"Two documents give different values for {field_name.replace('_', ' ')}.",
        values=[left_text, right_text],
        evidence=evidence or [],
        needs_reasoning=True,
    )

    try:
        run = agent.assess(candidate)
    except Exception as exc:  # noqa: BLE001 - an unavailable model must not decide the answer
        logger.warning("semantic comparison unavailable (%s); leaving undetermined", type(exc).__name__)
        return undetermined(
            "semantic",
            "semantic comparison could not be performed; manual review required",
            left=left_text,
            right=right_text,
        )

    assessment = run.data
    if assessment.classification == DiscrepancyClassification.NOT_A_DISCREPANCY:
        return equal(
            "semantic",
            assessment.explanation or "assessed as equivalent",
            similarity=assessment.confidence,
            left=left_text,
            right=right_text,
            cosmetic=True,
        )
    if assessment.classification == DiscrepancyClassification.CONFIRMED:
        return different(
            "semantic",
            assessment.explanation or "assessed as a genuine difference",
            similarity=1.0 - assessment.confidence,
            left=left_text,
            right=right_text,
        )
    return undetermined(
        "semantic",
        assessment.explanation or "the difference could not be resolved from the evidence",
        similarity=assessment.confidence,
        left=left_text,
        right=right_text,
    )

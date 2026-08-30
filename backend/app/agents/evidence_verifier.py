"""Second-pass verification of the findings that matter most.

Only HIGH severity findings and the ones the reasoner was unsure about get
here. The question is narrow and checkable: does the cited document actually
contain the claimed value on the claimed page. A finding that survives this is
one a reviewer can be handed; one that does not is either corrected or sent
back for manual reading.

The verifier can retract a finding but cannot create one, and its correction
is only applied when it names a value that appears in the supplied evidence.
"""

from __future__ import annotations

import json
import logging

from app.agents.base_agent import TEMPERATURE_REASONING, AgentRun, BaseAgent, clip, render
from app.models.enums import Severity, VerificationRecommendation
from app.schemas.discrepancy import (
    CandidateDiscrepancy,
    DiscrepancyAssessment,
    VerificationResult,
)

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CHARS = 2000


def needs_verification(
    candidate: CandidateDiscrepancy, assessment: DiscrepancyAssessment | None
) -> bool:
    """Which findings are worth a second call.

    HIGH severity always, because that is what changes a decision. Otherwise
    only where the first pass was uncertain — verifying a confidently dismissed
    formatting difference buys nothing.
    """
    if candidate.severity == Severity.HIGH:
        return True
    if assessment is None:
        return False
    if assessment.severity == Severity.HIGH:
        return True
    return assessment.classification == "UNCERTAIN"


class EvidenceVerifierAgent(BaseAgent):
    prompt_name = "evidence_verifier"
    temperature = TEMPERATURE_REASONING

    def verify(
        self,
        candidate: CandidateDiscrepancy,
        assessment: DiscrepancyAssessment | None,
        *,
        page_texts: dict[str, str] | None = None,
    ) -> AgentRun[VerificationResult]:
        """``page_texts`` maps "document_id:page" to that page's text.

        Passing the actual page text is what makes this a verification rather
        than a second opinion: the agent re-reads the source instead of
        re-reasoning about the same summary.
        """
        finding = {
            "type": candidate.type,
            "field": candidate.field,
            "severity": str(assessment.severity if assessment else candidate.severity),
            "classification": str(assessment.classification) if assessment else "CANDIDATE",
            "summary": candidate.summary,
            "values": candidate.values,
            "explanation": assessment.explanation if assessment else "",
        }

        evidence_payload = []
        for ref in candidate.evidence:
            key = f"{ref.document_id}:{ref.page}"
            evidence_payload.append(
                {
                    "document": ref.document_name or ref.document_id,
                    "page": ref.page,
                    "claimed_value": ref.value,
                    "page_text": clip((page_texts or {}).get(key, ref.snippet), 800),
                }
            )

        system = render(
            self.system_prompt,
            {
                "finding": json.dumps(finding, separators=(",", ":")),
                "evidence": clip(
                    json.dumps(evidence_payload, separators=(",", ":")), MAX_EVIDENCE_CHARS
                ),
            },
        )
        prompt = (
            "Check this finding against the supplied page text. Confirm only what the text "
            "actually shows. If the page text does not contain the claimed value, say so."
        )
        return self._run(VerificationResult, prompt=prompt, system=system)


def apply_verification(
    candidate: CandidateDiscrepancy,
    assessment: DiscrepancyAssessment | None,
    verification: VerificationResult,
) -> tuple[bool, str]:
    """Decide what a verification result means for the finding.

    Returns ``(keep, reason)``. REMOVE is honoured only when the verifier
    positively reports the evidence does not support the finding; an unverified
    result keeps the finding and routes it to a human, because "I could not
    confirm this" is not the same as "this is fine".
    """
    if verification.final_recommendation == VerificationRecommendation.REMOVE:
        if verification.verified is False and verification.reason:
            return False, f"withdrawn after evidence verification: {verification.reason}"
        return True, "removal was recommended without a supporting reason; kept for review"

    if verification.final_recommendation == VerificationRecommendation.MANUAL_REVIEW:
        return True, "evidence could not be confirmed automatically; manual review required"

    return True, verification.reason or "evidence verified"

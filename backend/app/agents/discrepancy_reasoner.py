"""Judging candidate discrepancies.

The agent never goes looking for problems. It is handed one candidate that
deterministic comparison already produced, along with the evidence behind it,
and asked a single question: is this a real difference, and how much does it
matter. It cannot add findings, and anything it returns about a finding it was
not given is discarded by the caller.

Its severity is also not the last word. A model saying HIGH does not make a
formatting difference high, so the workflow caps severity using the
deterministic severity policy.
"""

from __future__ import annotations

import json
import logging

from app.agents.base_agent import TEMPERATURE_REASONING, AgentRun, BaseAgent, clip, render
from app.models.enums import DiscrepancyClassification
from app.schemas.discrepancy import CandidateDiscrepancy, DiscrepancyAssessment, EvidenceRef

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CHARS = 1200


class DiscrepancyReasonerAgent(BaseAgent):
    prompt_name = "discrepancy_reasoner"
    temperature = TEMPERATURE_REASONING

    def assess(
        self, candidate: CandidateDiscrepancy
    ) -> AgentRun[DiscrepancyAssessment]:
        evidence_payload = [
            {
                "document": ref.document_name or ref.document_id,
                "document_type": ref.document_type,
                "page": ref.page,
                "field": ref.field,
                "value": ref.value,
                "quoted_text": clip(ref.snippet, 240),
            }
            for ref in candidate.evidence
        ]

        system = render(
            self.system_prompt,
            {
                "candidate": json.dumps(candidate.compact(), separators=(",", ":")),
                "evidence": clip(
                    json.dumps(evidence_payload, separators=(",", ":")), MAX_EVIDENCE_CHARS
                ),
            },
        )
        prompt = (
            "Assess this single candidate discrepancy. Do not introduce any other finding. "
            "If the difference is only formatting, capitalisation, punctuation or an "
            "abbreviation, it is not a discrepancy."
        )

        run = self._run(DiscrepancyAssessment, prompt=prompt, system=system)
        run.data = self._keep_evidence(run.data, candidate.evidence)
        return run

    @staticmethod
    def _keep_evidence(
        assessment: DiscrepancyAssessment, original: list[EvidenceRef]
    ) -> DiscrepancyAssessment:
        """Evidence stays as the deterministic layer recorded it.

        A model rewriting a page number or a quoted value would break the one
        guarantee the UI depends on: that clicking a flag lands on the text the
        flag is about.
        """
        return assessment.model_copy(update={"evidence": original})


def cap_severity(candidate: CandidateDiscrepancy, assessment: DiscrepancyAssessment) -> str:
    """The deterministic severity wins where the two disagree upward.

    A model may downgrade a finding it can explain away, but it may not
    escalate one beyond what the rule that produced it considered possible.
    """
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    if order.get(assessment.severity, 1) > order.get(candidate.severity, 1):
        return str(candidate.severity)
    return str(assessment.severity)


def is_dismissed(assessment: DiscrepancyAssessment) -> bool:
    return assessment.classification == DiscrepancyClassification.NOT_A_DISCREPANCY

"""Building the canonical applicant profile.

Consolidation is done twice, on purpose. Python does it first and
authoritatively: values are grouped by canonical field, compared with the same
comparison engine the rule engine uses, and a status of CONFIRMED,
CONFLICTING, UNCERTAIN or NOT_FOUND falls out of that comparison. Nothing about
that step is a judgement call, so nothing about it should depend on a model.

The agent is then asked one narrow question it is actually suited to: when
documents genuinely disagree, which value should lead. Its answer is accepted
only if it is one of the values a document actually asserted. Everything else
it returns is discarded. A conflict is never resolved away — both values stay
on the field with their sources, and the rule engine still raises the finding.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base_agent import TEMPERATURE_REASONING, AgentRun, BaseAgent, render
from app.comparison import ComparisonVerdict, compare_field
from app.models.enums import FieldStatus
from app.schemas.applicant import (
    CANONICAL_FIELDS,
    ApplicantProfileSchema,
    ProfileField,
    ProfileSource,
)
from app.utils.normalize import normalize_field

logger = logging.getLogger(__name__)

# Which document types are the better authority for which field, used only to
# order candidates for presentation. It never suppresses a conflict.
SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    "name": ("PAN", "AADHAAR", "PASSPORT", "DRIVING_LICENSE", "IDENTITY_PROOF"),
    "date_of_birth": ("AADHAAR", "PAN", "PASSPORT", "DRIVING_LICENSE"),
    "pan": ("PAN", "ITR", "TAX_DOCUMENT"),
    "aadhaar": ("AADHAAR",),
    "passport": ("PASSPORT",),
    "driving_license": ("DRIVING_LICENSE",),
    "current_address": ("AADHAAR", "ADDRESS_PROOF", "PASSPORT", "BANK_STATEMENT"),
    "permanent_address": ("PASSPORT", "AADHAAR", "ADDRESS_PROOF"),
    "income": ("ITR", "SALARY_SLIP", "EMPLOYMENT_PROOF"),
    "employer": ("SALARY_SLIP", "EMPLOYMENT_PROOF"),
    "bank_account": ("BANK_STATEMENT",),
    "loan_amount": ("LOAN_APPLICATION",),
}

# Confidence at or below which a lone value is reported as UNCERTAIN rather
# than CONFIRMED, even when nothing contradicts it.
LOW_CONFIDENCE = 0.5


class ProfileAgentField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str = ""
    status: str = "UNCERTAIN"
    confidence: float = 0.0
    reason: str = ""


class ProfileAgentOutput(BaseModel):
    """What the consolidation agent may return: a preferred value per field."""

    model_config = ConfigDict(extra="ignore")

    fields: dict[str, ProfileAgentField] = Field(default_factory=dict)


def collect_candidates(
    extractions: list[dict],
) -> dict[str, list[ProfileSource]]:
    """Group extracted values by canonical field.

    ``extractions`` is a list of dicts with keys: document_id, document_name,
    document_type, and fields (canonical_name, value, confidence, page,
    snippet, bbox).
    """
    candidates: dict[str, list[ProfileSource]] = defaultdict(list)
    for extraction in extractions:
        for item in extraction.get("fields", []):
            canonical = item.get("canonical_field")
            value = (item.get("value") or "").strip()
            if not canonical or canonical not in CANONICAL_FIELDS or not value:
                continue
            candidates[canonical].append(
                ProfileSource(
                    document_id=str(extraction.get("document_id", "")),
                    document_name=str(extraction.get("document_name", "")),
                    document_type=str(extraction.get("document_type", "")),
                    page=int(item.get("page") or 0),
                    value=value,
                    normalized_value=item.get("normalized_value") or "",
                    confidence=float(item.get("confidence") or 0.0),
                    snippet=item.get("snippet") or "",
                    bbox=item.get("bbox") or [],
                )
            )
    return dict(candidates)


def consolidate(candidates: dict[str, list[ProfileSource]]) -> ApplicantProfileSchema:
    """Deterministic consolidation. This is the authoritative pass."""
    fields: dict[str, ProfileField] = {}

    for field_name in CANONICAL_FIELDS:
        sources = candidates.get(field_name, [])
        if not sources:
            fields[field_name] = ProfileField(status=FieldStatus.NOT_FOUND)
            continue

        ordered = _order_by_priority(field_name, sources)
        groups = _group_equivalent(field_name, ordered)
        distinct_values = [group[0].value for group in groups]
        headline = groups[0][0]

        if len(groups) > 1:
            status = FieldStatus.CONFLICTING
            confidence = min(source.confidence for source in ordered)
        elif headline.confidence <= LOW_CONFIDENCE:
            status = FieldStatus.UNCERTAIN
            confidence = headline.confidence
        else:
            status = FieldStatus.CONFIRMED
            # Agreement across independent documents is worth more than any
            # single reading, but never enough to claim certainty.
            confidence = min(0.99, max(s.confidence for s in ordered) + 0.05 * (len(ordered) - 1))

        normalized = normalize_field(field_name, headline.value)
        fields[field_name] = ProfileField(
            value=headline.value,
            normalized_value=normalized.normalized or "",
            status=status,
            confidence=round(confidence, 3),
            sources=ordered,
            candidates=distinct_values,
        )

    return ApplicantProfileSchema(fields=fields)


def _order_by_priority(field_name: str, sources: list[ProfileSource]) -> list[ProfileSource]:
    priority = SOURCE_PRIORITY.get(field_name, ())

    def rank(source: ProfileSource) -> tuple[int, float]:
        try:
            position = priority.index(source.document_type)
        except ValueError:
            position = len(priority)
        return (position, -source.confidence)

    return sorted(sources, key=rank)


def _group_equivalent(
    field_name: str, sources: list[ProfileSource]
) -> list[list[ProfileSource]]:
    """Cluster values that the comparison engine considers the same thing.

    An UNDETERMINED comparison is treated as a distinct group, so an unresolved
    similarity surfaces as a conflict for review rather than being quietly
    merged into agreement.
    """
    groups: list[list[ProfileSource]] = []
    for source in sources:
        placed = False
        for group in groups:
            outcome = compare_field(field_name, group[0].value, source.value)
            if outcome.verdict == ComparisonVerdict.EQUAL:
                group.append(source)
                placed = True
                break
        if not placed:
            groups.append([source])
    return groups


class ProfileBuilderAgent(BaseAgent):
    prompt_name = "profile_builder"
    temperature = TEMPERATURE_REASONING

    def build(
        self, candidates: dict[str, list[ProfileSource]]
    ) -> AgentRun[ApplicantProfileSchema]:
        """Consolidate deterministically, then let the agent order conflicts."""
        profile = consolidate(candidates)
        conflicted = profile.conflicting_fields()

        if not conflicted:
            # Nothing to judge. Skipping the call is the whole point of running
            # the deterministic pass first.
            return AgentRun(
                data=profile,
                model="none",
                prompt_version=self.version,
                attempts=0,
            )

        payload = {
            name: [
                {
                    "value": source.value,
                    "document_type": source.document_type,
                    "document": source.document_name,
                    "page": source.page,
                    "confidence": source.confidence,
                }
                for source in profile.fields[name].sources
            ]
            for name in conflicted
        }

        system = render(
            self.system_prompt,
            {
                "extractions": json.dumps(payload, separators=(",", ":")),
                "source_priority": json.dumps(
                    {k: list(v) for k, v in SOURCE_PRIORITY.items() if k in conflicted},
                    separators=(",", ":"),
                ),
            },
        )
        prompt = (
            "These fields have conflicting values across documents. For each one, say which "
            "of the supplied values should be shown first, and why. Do not merge, correct or "
            "invent values, and do not resolve the conflict itself; both values remain on file."
        )

        run = self._run(ProfileAgentOutput, prompt=prompt, system=system)
        return AgentRun(
            data=self._apply_preferences(profile, run.data),
            model=run.model,
            prompt_version=run.prompt_version,
            attempts=run.attempts,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            latency_ms=run.latency_ms,
        )

    @staticmethod
    def _apply_preferences(
        profile: ApplicantProfileSchema, agent_output: ProfileAgentOutput
    ) -> ApplicantProfileSchema:
        """Accept a preference only when it names a value a document asserted.

        This is the guard that makes the call safe: anything the agent invents
        fails the membership check and is dropped, and the status the
        deterministic pass assigned is never overwritten.
        """
        for name, preference in (agent_output.fields or {}).items():
            field = profile.fields.get(name)
            if field is None or not field.is_conflicting:
                continue
            chosen = (preference.value or "").strip()
            if not chosen:
                continue
            match = next(
                (source for source in field.sources if source.value.strip() == chosen), None
            )
            if match is None:
                logger.warning(
                    "profile agent proposed a value for %s that no document contains; ignoring",
                    name,
                )
                continue
            field.value = match.value
            field.normalized_value = normalize_field(name, match.value).normalized or ""
            field.sources = [match, *[s for s in field.sources if s is not match]]
        return profile

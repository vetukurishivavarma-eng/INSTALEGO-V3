"""Rule configuration, the rule context, and the engine that runs them.

Rules are plain Python functions registered by category. Each receives the same
context — the canonical profile, the documents, the extracted field values and
the bank's configuration — and returns outcomes. No rule calls a model, and no
rule reaches into the database.

Configuration is loaded once per bank and deep-merged over the defaults, so a
bank file states only its differences and the resulting version string
identifies exactly which rule set produced a finding.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field as dataclass_field
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from app.config import settings
from app.models.enums import RuleResult, Severity
from app.schemas.applicant import ApplicantProfileSchema
from app.schemas.discrepancy import CandidateDiscrepancy, EvidenceRef

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge. Lists replace wholesale rather than concatenating.

    A bank that lists required_documents means exactly that list, not the
    defaults plus its own — appending would silently keep a requirement the
    bank meant to drop.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@dataclass(frozen=True)
class RuleConfig:
    """Resolved configuration for one bank."""

    bank_id: str
    version: str
    data: dict[str, Any]

    def rule(self, category: str, name: str) -> dict[str, Any]:
        return (self.data.get(category) or {}).get(name) or {}

    def enabled(self, category: str, name: str) -> bool:
        return bool(self.rule(category, name).get("enabled", True))

    def severity(self, category: str, name: str, fallback: str = Severity.MEDIUM) -> str:
        return str(self.rule(category, name).get("severity", fallback))

    @property
    def required_documents(self) -> list[str]:
        return list(self.data.get("required_documents") or [])

    @property
    def document_type_groups(self) -> dict[str, list[str]]:
        return dict(self.data.get("document_type_groups") or {})

    @property
    def thresholds(self) -> dict[str, float]:
        return dict((self.data.get("comparison") or {}).get("thresholds") or {})

    @property
    def semantic_escalation(self) -> bool:
        return bool((self.data.get("comparison") or {}).get("semantic_escalation", True))

    @property
    def status_policy(self) -> dict[str, Any]:
        return dict(self.data.get("status_policy") or {})

    @property
    def report_template(self) -> str:
        return str((self.data.get("report") or {}).get("template", "default"))


@lru_cache(maxsize=16)
def load_rule_config(bank_id: str | None = None) -> RuleConfig:
    """Defaults, with the bank's overrides merged over them."""
    default_path = settings.CONFIG_DIR / "default_rules.yaml"
    if not default_path.exists():
        raise FileNotFoundError(f"default rule configuration missing: {default_path}")

    data = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}
    version = str(data.get("version", "default-v1"))
    resolved_bank = bank_id or settings.DEFAULT_BANK_ID

    if resolved_bank and resolved_bank != "default":
        bank_path = settings.bank_config_dir / f"{resolved_bank}.yaml"
        if bank_path.exists():
            override = yaml.safe_load(bank_path.read_text(encoding="utf-8")) or {}
            data = _deep_merge(data, override)
            version = str(override.get("version", f"{resolved_bank}-v1"))
        else:
            # Falling back silently would let a typo in bank_id quietly apply
            # the wrong rules to a real case.
            logger.warning(
                "no configuration for bank %r at %s; using defaults", resolved_bank, bank_path
            )

    return RuleConfig(bank_id=resolved_bank, version=version, data=data)


def reset_rule_config_cache() -> None:
    load_rule_config.cache_clear()


# --------------------------------------------------------------------------
# Context and outcomes
# --------------------------------------------------------------------------
@dataclass
class DocumentView:
    """What a rule is allowed to know about an uploaded document."""

    document_id: str
    filename: str
    document_type: str
    subtype: str = ""
    page_count: int = 0
    sha256: str = ""
    is_readable: bool = True
    status: str = ""
    quality_flags: list[str] = dataclass_field(default_factory=list)
    classification_confidence: float = 0.0
    ocr_confidences: list[float] = dataclass_field(default_factory=list)
    error_code: str | None = None


@dataclass
class FieldObservation:
    """One extracted value, tied to the document and page it came from."""

    canonical_field: str
    raw_field: str
    value: str
    normalized_value: str | None
    confidence: float
    document_id: str
    document_name: str
    document_type: str
    page: int = 0
    snippet: str = ""
    bbox: list[float] = dataclass_field(default_factory=list)

    def as_evidence(self) -> EvidenceRef:
        return EvidenceRef(
            document_id=self.document_id,
            document_name=self.document_name,
            document_type=self.document_type,
            page=self.page,
            field=self.canonical_field,
            value=self.value,
            snippet=self.snippet,
            bbox=self.bbox,
        )


@dataclass
class RuleContext:
    profile: ApplicantProfileSchema
    documents: list[DocumentView]
    observations: list[FieldObservation]
    config: RuleConfig
    as_of: date = dataclass_field(default_factory=date.today)

    def values_for(self, canonical_field: str) -> list[FieldObservation]:
        return [o for o in self.observations if o.canonical_field == canonical_field]

    def documents_of_type(self, document_type: str) -> list[DocumentView]:
        return [d for d in self.documents if d.document_type == document_type]

    def observation_for_document(
        self, document_id: str, canonical_field: str
    ) -> FieldObservation | None:
        for observation in self.observations:
            if observation.document_id == document_id and observation.canonical_field == canonical_field:
                return observation
        return None


@dataclass
class RuleOutcome:
    """The result of one rule evaluation, plus any finding it produced."""

    rule_id: str
    category: str
    result: RuleResult
    field: str | None = None
    severity: str | None = None
    reason: str = ""
    evidence: list[EvidenceRef] = dataclass_field(default_factory=list)
    candidate: CandidateDiscrepancy | None = None

    def to_row(self, rules_version: str) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_category": self.category,
            "result": str(self.result),
            "field": self.field,
            "severity": self.severity,
            "reason": self.reason,
            "evidence": [ref.model_dump() for ref in self.evidence],
            "rules_version": rules_version,
        }


RuleFunction = Callable[[RuleContext], Iterable[RuleOutcome]]

_REGISTRY: dict[str, list[tuple[str, RuleFunction]]] = {}


def rule(category: str, name: str) -> Callable[[RuleFunction], RuleFunction]:
    """Register a rule function under a category."""

    def decorator(func: RuleFunction) -> RuleFunction:
        _REGISTRY.setdefault(category, []).append((name, func))
        return func

    return decorator


def registered_rules() -> dict[str, list[str]]:
    return {category: [name for name, _ in items] for category, items in _REGISTRY.items()}


class RuleEngine:
    """Runs every enabled rule and collects outcomes and candidate findings."""

    def __init__(self, config: RuleConfig) -> None:
        self.config = config

    def run(self, context: RuleContext) -> list[RuleOutcome]:
        # Importing here registers the rule modules exactly once.
        from app.rules import dates, documents, financial, identity  # noqa: F401

        outcomes: list[RuleOutcome] = []
        for category, items in _REGISTRY.items():
            for name, func in items:
                if not self.config.enabled(category, name):
                    outcomes.append(
                        RuleOutcome(
                            rule_id=f"{category}.{name}",
                            category=category,
                            result=RuleResult.NOT_APPLICABLE,
                            reason="disabled by configuration",
                        )
                    )
                    continue
                try:
                    outcomes.extend(func(context))
                except Exception:  # noqa: BLE001
                    # One broken rule must not lose the other findings; the
                    # failure is recorded as a rule outcome of its own.
                    logger.exception("rule %s.%s failed", category, name)
                    outcomes.append(
                        RuleOutcome(
                            rule_id=f"{category}.{name}",
                            category=category,
                            result=RuleResult.REVIEW,
                            reason="the rule could not be evaluated; manual review required",
                        )
                    )
        return outcomes

    @staticmethod
    def candidates(outcomes: Iterable[RuleOutcome]) -> list[CandidateDiscrepancy]:
        return [outcome.candidate for outcome in outcomes if outcome.candidate is not None]

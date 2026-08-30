"""The deterministic validation layer.

Importing this package registers every rule with the engine.
"""

from app.rules import dates, documents, financial, identity, land  # noqa: F401
from app.rules.registry import (
    DocumentView,
    FieldObservation,
    RuleConfig,
    RuleContext,
    RuleEngine,
    RuleOutcome,
    load_rule_config,
    registered_rules,
    reset_rule_config_cache,
    rule,
)
from app.rules.status import StatusDecision, decide_status, overall_confidence

__all__ = [
    "DocumentView",
    "FieldObservation",
    "RuleConfig",
    "RuleContext",
    "RuleEngine",
    "RuleOutcome",
    "StatusDecision",
    "decide_status",
    "load_rule_config",
    "overall_confidence",
    "registered_rules",
    "reset_rule_config_cache",
    "rule",
]

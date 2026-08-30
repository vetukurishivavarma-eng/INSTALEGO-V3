"""The shape every comparison returns.

Three answers are possible, and the third one carries most of the value:
equal, different, or undetermined. A comparison that cannot decide must say so
rather than defaulting to "different", because a false mismatch costs a
reviewer more than a missed one — it buries the real findings under noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComparisonVerdict(StrEnum):
    EQUAL = "EQUAL"
    DIFFERENT = "DIFFERENT"
    UNDETERMINED = "UNDETERMINED"
    NOT_COMPARABLE = "NOT_COMPARABLE"


@dataclass
class ComparisonOutcome:
    verdict: ComparisonVerdict
    method: str
    reason: str
    similarity: float | None = None
    left: str = ""
    right: str = ""
    # Set when a difference is real but immaterial: formatting, case,
    # punctuation, an expanded initial. Severity logic uses this to keep
    # cosmetic variation out of the HIGH band.
    cosmetic: bool = False

    @property
    def is_equal(self) -> bool:
        return self.verdict == ComparisonVerdict.EQUAL

    @property
    def is_different(self) -> bool:
        return self.verdict == ComparisonVerdict.DIFFERENT

    @property
    def needs_judgement(self) -> bool:
        return self.verdict == ComparisonVerdict.UNDETERMINED


def equal(method: str, reason: str, *, similarity: float | None = None,
          left: str = "", right: str = "", cosmetic: bool = False) -> ComparisonOutcome:
    return ComparisonOutcome(
        verdict=ComparisonVerdict.EQUAL, method=method, reason=reason,
        similarity=similarity, left=left, right=right, cosmetic=cosmetic,
    )


def different(method: str, reason: str, *, similarity: float | None = None,
              left: str = "", right: str = "") -> ComparisonOutcome:
    return ComparisonOutcome(
        verdict=ComparisonVerdict.DIFFERENT, method=method, reason=reason,
        similarity=similarity, left=left, right=right,
    )


def undetermined(method: str, reason: str, *, similarity: float | None = None,
                 left: str = "", right: str = "") -> ComparisonOutcome:
    return ComparisonOutcome(
        verdict=ComparisonVerdict.UNDETERMINED, method=method, reason=reason,
        similarity=similarity, left=left, right=right,
    )


def not_comparable(method: str, reason: str, *, left: str = "", right: str = "") -> ComparisonOutcome:
    return ComparisonOutcome(
        verdict=ComparisonVerdict.NOT_COMPARABLE, method=method, reason=reason,
        left=left, right=right,
    )

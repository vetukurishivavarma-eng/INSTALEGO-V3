"""Exact comparison, for values where any difference is a real difference.

Identifiers, account numbers, dates and amounts. A PAN that differs by one
character is a different PAN; there is no similarity score worth computing and
no judgement call to hand to a model.

The only softening applied here is on ambiguity in the source: a date written
12/04/1998 does not exactly disagree with 4 December 1998, because the first
one was never unambiguous to begin with. That returns UNDETERMINED, not
DIFFERENT.
"""

from __future__ import annotations

from app.comparison.base import ComparisonOutcome, different, equal, not_comparable, undetermined
from app.utils.dates import same_date
from app.utils.numbers import amounts_equal
from app.utils.normalize import normalize_value


def compare_identifier(kind: str, left: str | None, right: str | None) -> ComparisonOutcome:
    normalized_left = normalize_value(kind, left)
    normalized_right = normalize_value(kind, right)

    if not normalized_left.comparable or not normalized_right.comparable:
        return not_comparable("exact", "one side has no comparable value",
                              left=str(left or ""), right=str(right or ""))

    if normalized_left.normalized == normalized_right.normalized:
        cosmetic = normalized_left.original.strip() != normalized_right.original.strip()
        return equal(
            "exact",
            "identifiers match" + (" after removing formatting" if cosmetic else ""),
            similarity=1.0,
            left=normalized_left.original,
            right=normalized_right.original,
            cosmetic=cosmetic,
        )

    return different(
        "exact",
        f"{normalized_left.normalized} does not match {normalized_right.normalized}",
        similarity=0.0,
        left=normalized_left.original,
        right=normalized_right.original,
    )


def compare_date(left: str | None, right: str | None) -> ComparisonOutcome:
    result, reason = same_date(left, right)
    left_text, right_text = str(left or ""), str(right or "")

    if result is None:
        if "parse" in reason:
            return not_comparable("exact", reason, left=left_text, right=right_text)
        return undetermined("exact", reason, left=left_text, right=right_text)
    if result:
        cosmetic = left_text.strip() != right_text.strip()
        return equal("exact", reason, similarity=1.0, left=left_text, right=right_text,
                     cosmetic=cosmetic)
    return different("exact", reason, similarity=0.0, left=left_text, right=right_text)


def compare_amount(
    left: str | None,
    right: str | None,
    *,
    tolerance_pct: float = 0.0,
    tolerance_abs: float = 0.0,
) -> ComparisonOutcome:
    result, reason = amounts_equal(
        left, right, tolerance_pct=tolerance_pct, tolerance_abs=tolerance_abs
    )
    left_text, right_text = str(left or ""), str(right or "")

    if result is None:
        return not_comparable("exact", reason, left=left_text, right=right_text)
    if result:
        cosmetic = left_text.strip() != right_text.strip()
        return equal("exact", reason, similarity=1.0, left=left_text, right=right_text,
                     cosmetic=cosmetic)
    return different("exact", reason, similarity=0.0, left=left_text, right=right_text)


def compare_exact_text(left: str | None, right: str | None) -> ComparisonOutcome:
    normalized_left = normalize_value("text", left)
    normalized_right = normalize_value("text", right)

    if not normalized_left.comparable or not normalized_right.comparable:
        return not_comparable("exact", "one side has no comparable value",
                              left=str(left or ""), right=str(right or ""))
    if normalized_left.normalized == normalized_right.normalized:
        return equal("exact", "values match", similarity=1.0,
                     left=normalized_left.original, right=normalized_right.original,
                     cosmetic=normalized_left.original != normalized_right.original)
    return different("exact", "values differ", similarity=0.0,
                     left=normalized_left.original, right=normalized_right.original)

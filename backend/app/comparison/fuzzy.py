"""Fuzzy comparison for names, addresses and organisation names.

These are the values where a difference is usually not a discrepancy. The same
person is written RAVI KUMAR, Ravi Kumar, Mr. Ravi Kumar and Ravi K Kumar
across four documents, and flagging that as an identity mismatch is the single
easiest way to make a review queue useless.

Every comparison here has a middle band. Above it, equal; below it, different;
inside it, nobody decides deterministically and the case goes to the reasoning
agent with both values attached.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.comparison.base import ComparisonOutcome, different, equal, not_comparable, undetermined
from app.utils.normalize import address_pincode, normalize_address
from app.utils.text import (
    initials_expansion_match,
    name_tokens,
    normalize_name,
    similarity,
    token_set_similarity,
)


@dataclass(frozen=True)
class FuzzyThresholds:
    """Tunable per bank through configuration."""

    name_equal: float = 0.92
    name_different: float = 0.70
    address_equal: float = 0.90
    address_different: float = 0.55
    organisation_equal: float = 0.88
    organisation_different: float = 0.60


DEFAULT_THRESHOLDS = FuzzyThresholds()

# Corporate suffixes carry no identifying information and appear inconsistently.
_ORG_NOISE = {
    "pvt", "private", "ltd", "limited", "llp", "inc", "incorporated", "corp",
    "corporation", "co", "company", "technologies", "technology", "services",
    "solutions", "and", "&",
}


def compare_name(
    left: str | None, right: str | None, *, thresholds: FuzzyThresholds = DEFAULT_THRESHOLDS
) -> ComparisonOutcome:
    left_text, right_text = str(left or ""), str(right or "")
    left_norm, right_norm = normalize_name(left_text), normalize_name(right_text)

    if not left_norm or not right_norm:
        return not_comparable("fuzzy", "one side has no usable name",
                              left=left_text, right=right_text)

    if left_norm == right_norm:
        return equal(
            "fuzzy",
            "names match once case, spacing and honorifics are set aside",
            similarity=1.0,
            left=left_text,
            right=right_text,
            cosmetic=left_text.strip() != right_text.strip(),
        )

    left_tokens, right_tokens = name_tokens(left_text), name_tokens(right_text)

    if initials_expansion_match(left_tokens, right_tokens):
        return equal(
            "fuzzy",
            "one name abbreviates the other; the name parts are consistent",
            similarity=0.95,
            left=left_text,
            right=right_text,
            cosmetic=True,
        )

    if set(left_tokens) == set(right_tokens):
        return equal(
            "fuzzy",
            "the same name parts appear in a different order",
            similarity=0.97,
            left=left_text,
            right=right_text,
            cosmetic=True,
        )

    score = max(similarity(left_norm, right_norm), token_set_similarity(left_text, right_text))

    if score >= thresholds.name_equal:
        return equal("fuzzy", f"names are near-identical (similarity {score:.2f})",
                     similarity=score, left=left_text, right=right_text, cosmetic=True)
    if score <= thresholds.name_different:
        return different("fuzzy", f"names differ substantially (similarity {score:.2f})",
                         similarity=score, left=left_text, right=right_text)
    return undetermined(
        "fuzzy",
        f"names are similar but not equivalent (similarity {score:.2f})",
        similarity=score,
        left=left_text,
        right=right_text,
    )


def compare_address(
    left: str | None, right: str | None, *, thresholds: FuzzyThresholds = DEFAULT_THRESHOLDS
) -> ComparisonOutcome:
    left_text, right_text = str(left or ""), str(right or "")
    left_norm, right_norm = normalize_address(left_text), normalize_address(right_text)

    if not left_norm or not right_norm:
        return not_comparable("fuzzy", "one side has no usable address",
                              left=left_text, right=right_text)

    if left_norm == right_norm:
        return equal("fuzzy", "addresses match after formatting is normalised",
                     similarity=1.0, left=left_text, right=right_text,
                     cosmetic=left_text.strip() != right_text.strip())

    left_pin, right_pin = address_pincode(left_text), address_pincode(right_text)
    score = max(similarity(left_norm, right_norm), token_set_similarity(left_norm, right_norm))

    # A differing PIN code is the one address signal precise enough to stand on
    # its own: two addresses in different postal areas are different addresses.
    if left_pin and right_pin and left_pin != right_pin:
        return different(
            "fuzzy",
            f"postal codes differ ({left_pin} against {right_pin})",
            similarity=score,
            left=left_text,
            right=right_text,
        )

    if score >= thresholds.address_equal:
        return equal("fuzzy", f"addresses are effectively identical (similarity {score:.2f})",
                     similarity=score, left=left_text, right=right_text, cosmetic=True)
    if score <= thresholds.address_different:
        return different("fuzzy", f"addresses differ substantially (similarity {score:.2f})",
                         similarity=score, left=left_text, right=right_text)
    # Same PIN, partial overlap: could be the same place written differently,
    # could be a different flat in the same block. Not a machine's call.
    return undetermined(
        "fuzzy",
        f"addresses overlap but are not equivalent (similarity {score:.2f})",
        similarity=score,
        left=left_text,
        right=right_text,
    )


def compare_organisation(
    left: str | None, right: str | None, *, thresholds: FuzzyThresholds = DEFAULT_THRESHOLDS
) -> ComparisonOutcome:
    left_text, right_text = str(left or ""), str(right or "")
    left_core = _organisation_core(left_text)
    right_core = _organisation_core(right_text)

    if not left_core or not right_core:
        return not_comparable("fuzzy", "one side has no usable organisation name",
                              left=left_text, right=right_text)

    if left_core == right_core:
        return equal("fuzzy", "organisation names match once legal suffixes are set aside",
                     similarity=1.0, left=left_text, right=right_text,
                     cosmetic=left_text.strip() != right_text.strip())

    score = max(similarity(left_core, right_core), token_set_similarity(left_core, right_core))
    if score >= thresholds.organisation_equal:
        return equal("fuzzy", f"organisation names are near-identical (similarity {score:.2f})",
                     similarity=score, left=left_text, right=right_text, cosmetic=True)
    if score <= thresholds.organisation_different:
        return different("fuzzy", f"organisation names differ (similarity {score:.2f})",
                         similarity=score, left=left_text, right=right_text)
    return undetermined("fuzzy", f"organisation names are similar (similarity {score:.2f})",
                        similarity=score, left=left_text, right=right_text)


def _organisation_core(value: str) -> str:
    tokens = [t for t in normalize_name(value).lower().split() if t not in _ORG_NOISE]
    return " ".join(tokens).upper()

"""Text and name normalisation, similarity, and log masking.

Normalisation here only ever produces a *comparison* form. The original value
is stored untouched alongside it, because a report has to quote what the
document actually said.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Honorifics and suffixes carry no identity information and appear
# inconsistently across documents, so they are dropped before comparison.
_TITLES = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "shri", "sri", "smt", "kum",
    "md", "sh", "late", "m/s",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_whitespace(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", value).strip()


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: str | None) -> str:
    """Case-folded, accent-stripped, whitespace-collapsed comparison form."""
    if not value:
        return ""
    return normalize_whitespace(strip_accents(value).upper())


def name_tokens(value: str | None) -> list[str]:
    """Meaningful name parts: no punctuation, no titles, no suffixes."""
    if not value:
        return []
    cleaned = _PUNCT.sub(" ", strip_accents(value).upper())
    tokens = [t for t in _WS.sub(" ", cleaned).strip().split(" ") if t]
    return [t for t in tokens if t.lower() not in _TITLES and t.lower() not in _SUFFIXES]


def normalize_name(value: str | None) -> str:
    return " ".join(name_tokens(value))


def _token_compatible(a: str, b: str) -> bool:
    """Two name parts can denote the same thing: equal, or one is the other's
    initial."""
    if a == b:
        return True
    if len(a) == 1 and b.startswith(a):
        return True
    return len(b) == 1 and a.startswith(b)


def initials_expansion_match(tokens_a: list[str], tokens_b: list[str]) -> bool:
    """True when one name abbreviates the other, e.g. RAVI K KUMAR vs RAVI KUMAR.

    A single letter may stand for a full token beginning with it, or be absent
    from the other name entirely. Matching needs backtracking: consuming KUMAR
    with the initial K would otherwise strand the real surname, so an
    alignment is searched rather than walked greedily.
    """
    if not tokens_a or not tokens_b:
        return False

    from functools import lru_cache

    left, right = tuple(tokens_a), tuple(tokens_b)

    @lru_cache(maxsize=None)
    def align(i: int, j: int) -> bool:
        if i == len(left):
            # Any name parts left over on the other side must be droppable
            # initials, never a surname the first name simply lacks.
            return all(len(token) == 1 for token in right[j:])
        if j == len(right):
            return all(len(token) == 1 for token in left[i:])

        if _token_compatible(left[i], right[j]) and align(i + 1, j + 1):
            return True
        if len(left[i]) == 1 and align(i + 1, j):
            return True
        return len(right[j]) == 1 and align(i, j + 1)

    return align(0, 0)


def similarity(a: str | None, b: str | None) -> float:
    """0.0-1.0 character similarity of the normalised forms."""
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def token_set_similarity(a: str | None, b: str | None) -> float:
    """Order-insensitive token overlap (Jaccard). Robust to reordered names."""
    set_a, set_b = set(name_tokens(a)), set(name_tokens(b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# --------------------------------------------------------------------------
# Log masking
# --------------------------------------------------------------------------
_MASK_PATTERNS = [
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "PAN"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "AADHAAR"),
    (re.compile(r"\b[A-Z]{1}\d{7}\b"), "PASSPORT"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "EMAIL"),
    (re.compile(r"\b(?:\+?91[\s-]?)?[6-9]\d{9}\b"), "PHONE"),
    (re.compile(r"\b\d{9,18}\b"), "ACCOUNT"),
]


def mask_value(value: str | None, keep: int = 4) -> str:
    """Mask an identifier, leaving the last few characters for reconciliation."""
    if not value:
        return ""
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def mask_sensitive(text: str | None) -> str:
    """Redact identifiers before anything reaches a log sink or audit row."""
    if not text:
        return ""
    masked = str(text)
    for pattern, label in _MASK_PATTERNS:
        masked = pattern.sub(lambda m, lbl=label: f"[{lbl}:{mask_value(m.group(0))}]", masked)
    return masked


def truncate(value: str | None, limit: int = 300) -> str:
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."

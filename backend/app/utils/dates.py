"""Date parsing and comparison.

Two properties matter more than coverage here. First, a date is only normalised
when the reading is unambiguous, or when a documented day-first convention
settles it. Second, ambiguity is reported rather than hidden, so a comparison
built on a guess can be downgraded to REVIEW instead of asserted as a mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# Ordered by specificity. Day-first variants come before month-first because
# the target document set (Indian banking and identity documents) is day-first.
_FORMATS: list[tuple[str, str]] = [
    ("%Y-%m-%d", "iso"),
    ("%d/%m/%Y", "dayfirst"),
    ("%d-%m-%Y", "dayfirst"),
    ("%d.%m.%Y", "dayfirst"),
    ("%d %b %Y", "textual"),
    ("%d %B %Y", "textual"),
    ("%b %d, %Y", "textual"),
    ("%B %d, %Y", "textual"),
    ("%d-%b-%Y", "textual"),
    ("%d/%m/%y", "dayfirst_short"),
    ("%d-%m-%y", "dayfirst_short"),
    ("%Y/%m/%d", "iso"),
]

_CLEAN = re.compile(r"[,\s]+")
_DIGIT_SEP = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})$")


@dataclass(frozen=True)
class ParsedDate:
    """A parse attempt and everything the caller needs to judge it."""

    raw: str
    value: date | None
    iso: str | None
    ambiguous: bool
    format_used: str | None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def _is_ambiguous(raw: str) -> bool:
    """True when both a day-first and a month-first reading are valid.

    ``12/04/1998`` is ambiguous; ``25/04/1998`` is not, because 25 cannot be a
    month. Callers use this to soften a mismatch into a review item.
    """
    match = _DIGIT_SEP.match(raw.strip())
    if not match:
        return False
    first, second, _ = match.groups()
    if len(first) == 4:  # already year-first
        return False
    try:
        a, b = int(first), int(second)
    except ValueError:
        return False
    return 1 <= a <= 12 and 1 <= b <= 12 and a != b


def parse_date(raw: str | None, *, dayfirst: bool = True) -> ParsedDate:
    """Parse a date string without ever inventing one.

    Returns a ParsedDate whose ``value`` is None when the input cannot be read
    confidently; callers must not fall back to a partial guess.
    """
    if not raw or not str(raw).strip():
        return ParsedDate(raw="", value=None, iso=None, ambiguous=False, format_used=None,
                          reason="empty")

    text = _CLEAN.sub(" ", str(raw).strip())
    ambiguous = _is_ambiguous(text)
    formats = _FORMATS if dayfirst else [("%m/%d/%Y", "monthfirst"), *_FORMATS]

    for fmt, kind in formats:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return ParsedDate(
            raw=str(raw),
            value=parsed,
            iso=parsed.isoformat(),
            ambiguous=ambiguous and kind.startswith("dayfirst"),
            format_used=fmt,
        )

    return ParsedDate(raw=str(raw), value=None, iso=None, ambiguous=ambiguous,
                      format_used=None, reason="unrecognised format")


def to_iso(raw: str | None) -> str | None:
    """ISO form, or None when the value is not unambiguously a date."""
    parsed = parse_date(raw)
    return parsed.iso


def same_date(a: str | None, b: str | None) -> tuple[bool | None, str]:
    """Compare two date strings.

    Returns ``(equal, reason)`` where ``equal`` is None when the comparison
    cannot be made — an unparseable side, or an ambiguity that would decide the
    answer. None means REVIEW, never FAIL.
    """
    left, right = parse_date(a), parse_date(b)
    if not left.ok or not right.ok:
        missing = "left" if not left.ok else "right"
        return None, f"could not parse the {missing} date"

    if left.value == right.value:
        return True, "dates match"

    # Differing, but one side was read under the day-first assumption: swapping
    # day and month may reconcile them, so a human decides.
    if left.ambiguous or right.ambiguous:
        if _swapped_equal(left, right):
            return None, "dates match only if one is read month-first; ambiguous source format"
    return False, f"{left.iso} does not match {right.iso}"


def _swapped_equal(left: ParsedDate, right: ParsedDate) -> bool:
    for side in (left, right):
        if side.value is None:
            return False
    try:
        swapped = date(left.value.year, left.value.day, left.value.month)
    except ValueError:
        return False
    return swapped == right.value


def is_expired(raw: str | None, *, as_of: date | None = None) -> tuple[bool | None, str]:
    """Whether an expiry date has passed. None when it cannot be determined."""
    parsed = parse_date(raw)
    if not parsed.ok:
        return None, "expiry date could not be parsed"
    today = as_of or date.today()
    if parsed.value < today:
        return True, f"expired on {parsed.iso}"
    return False, f"valid until {parsed.iso}"


def in_order(earlier: str | None, later: str | None) -> tuple[bool | None, str]:
    """Whether ``earlier`` precedes ``later``. None when undeterminable."""
    left, right = parse_date(earlier), parse_date(later)
    if not left.ok or not right.ok:
        return None, "one or both dates could not be parsed"
    if left.value <= right.value:
        return True, f"{left.iso} precedes {right.iso}"
    return False, f"{left.iso} is after {right.iso}"


def age_on(dob: str | None, as_of: date | None = None) -> int | None:
    parsed = parse_date(dob)
    if not parsed.ok:
        return None
    today = as_of or date.today()
    born = parsed.value
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

"""Field normalisation, dispatched by what kind of thing a field holds.

This is the boundary between what a document said and what can be compared.
Every value crossing it keeps both forms: ``original`` is quoted in reports and
shown in the UI, ``normalized`` is the only thing comparisons ever touch.

Normalisation is conservative by design. Addresses are cleaned but never
canonicalised into equality — "12 MG Road" and "12 M.G. Road" are made
comparable, while deciding whether they are the same place is left to a
comparison that can express uncertainty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field

from app.utils import identifiers
from app.utils.dates import parse_date
from app.utils.numbers import normalize_amount
from app.utils.text import normalize_name, normalize_text, normalize_whitespace

# Street-type abbreviations, expanded so that only spelling varies between two
# renderings of the same address. Deliberately short: an aggressive list starts
# merging genuinely different addresses.
_ADDRESS_EXPANSIONS = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "apt": "apartment",
    "apts": "apartments",
    "blk": "block",
    "bldg": "building",
    "flr": "floor",
    "opp": "opposite",
    "nr": "near",
    "no": "number",
    "sec": "sector",
    "ph": "phase",
    "extn": "extension",
    "cross": "cross",
    "main": "main",
}
_ADDRESS_PUNCT = re.compile(r"[.,;:#\-/\\()]+")
# A full stop straight after a single letter is an initial (M.G. Road), so the
# stop is deleted rather than turned into a separator. Turning it into a space
# would split the initials apart and stop them matching the undotted spelling.
_INITIAL_DOT = re.compile(r"\b([A-Za-z])\.")
_PIN = re.compile(r"\b(\d{6})\b")


@dataclass
class NormalizedValue:
    original: str
    normalized: str | None
    kind: str
    ambiguous: bool = False
    notes: list[str] = dataclass_field(default_factory=list)

    @property
    def comparable(self) -> bool:
        return bool(self.normalized)


def normalize_address(value: str | None) -> str:
    """Case, punctuation and street abbreviations only. Nothing semantic."""
    if not value:
        return ""
    text = normalize_text(value).lower()
    text = _INITIAL_DOT.sub(r"\1", text)
    text = _ADDRESS_PUNCT.sub(" ", text)
    tokens = [t for t in text.split() if t]
    expanded = [_ADDRESS_EXPANSIONS.get(token, token) for token in tokens]
    return " ".join(expanded).upper()


def address_pincode(value: str | None) -> str | None:
    """The 6-digit PIN, which is the one part of an address that compares
    exactly and carries most of the discriminating power."""
    if not value:
        return None
    match = _PIN.search(str(value))
    return match.group(1) if match else None


def normalize_value(kind: str, value: str | None) -> NormalizedValue:
    """Normalise ``value`` according to ``kind``.

    Returns a NormalizedValue whose ``normalized`` is None when the input
    cannot be reduced confidently, which downstream code treats as
    not-comparable rather than as an empty value.
    """
    original = "" if value is None else str(value)
    stripped = original.strip()

    if not stripped:
        return NormalizedValue(original=original, normalized=None, kind=kind, notes=["empty"])

    if kind == "name":
        normalized = normalize_name(stripped)
        return NormalizedValue(original=original, normalized=normalized or None, kind=kind)

    if kind == "date":
        parsed = parse_date(stripped)
        notes = [] if parsed.ok else [parsed.reason or "unparseable date"]
        return NormalizedValue(
            original=original,
            normalized=parsed.iso,
            kind=kind,
            ambiguous=parsed.ambiguous,
            notes=notes,
        )

    if kind == "amount":
        normalized = normalize_amount(stripped)
        notes = [] if normalized else ["unparseable amount"]
        return NormalizedValue(original=original, normalized=normalized, kind=kind, notes=notes)

    if kind == "address":
        normalized = normalize_address(stripped)
        return NormalizedValue(original=original, normalized=normalized or None, kind=kind)

    if kind in identifiers.NORMALIZERS:
        normalizer = identifiers.NORMALIZERS[kind]
        normalized = normalizer(stripped)
        result = NormalizedValue(original=original, normalized=normalized, kind=kind)
        checker = identifiers.CHECKERS.get(kind)
        if checker is not None:
            check = checker(stripped)
            if not check.valid_format:
                # Recorded, not rejected: a malformed identifier is a finding
                # for the rule engine, not a reason to discard the value.
                result.notes.append(check.reason)
        return result

    # Free text: whitespace and case only.
    return NormalizedValue(
        original=original, normalized=normalize_whitespace(stripped).upper() or None, kind="text"
    )


def kind_for_field(field_name: str) -> str:
    """Map a canonical profile field to its normalisation kind."""
    from app.schemas.applicant import FIELD_KINDS

    return FIELD_KINDS.get(field_name, "text")


def normalize_field(field_name: str, value: str | None) -> NormalizedValue:
    return normalize_value(kind_for_field(field_name), value)

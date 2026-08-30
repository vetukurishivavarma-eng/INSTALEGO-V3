"""Identifier normalisation and structural validation.

Formatting characters are stripped only where the issuing authority treats them
as decoration (spaces in an Aadhaar number), never where they carry meaning.
Leading zeros are always preserved: an account number is a string, not an int.

Structural validity is not identity verification. A well-formed PAN is not a
PAN that exists, and nothing in this module should be read as confirming that a
document is genuine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPACES = re.compile(r"[\s\-]")

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^[2-9][0-9]{11}$")
PASSPORT_IN_RE = re.compile(r"^[A-PR-WYa-pr-wy][1-9]\d\s?\d{4}[1-9]$")
DL_IN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}\s?[0-9]{4}[0-9]{7}$")
PHONE_IN_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Verhoeff tables. UIDAI uses this checksum for the twelfth Aadhaar digit, so a
# transposed pair from OCR is detectable rather than silently accepted.
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


@dataclass(frozen=True)
class IdentifierCheck:
    normalized: str | None
    valid_format: bool
    reason: str


def _strip(value: str | None) -> str:
    if not value:
        return ""
    return _SPACES.sub("", str(value)).strip()


def verhoeff_valid(digits: str) -> bool:
    checksum = 0
    for position, digit in enumerate(reversed(digits)):
        if not digit.isdigit():
            return False
        checksum = _D[checksum][_P[position % 8][int(digit)]]
    return checksum == 0


def normalize_pan(value: str | None) -> str | None:
    cleaned = _strip(value).upper()
    return cleaned or None


def check_pan(value: str | None) -> IdentifierCheck:
    cleaned = normalize_pan(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if PAN_RE.match(cleaned):
        return IdentifierCheck(cleaned, True, "well-formed PAN")
    return IdentifierCheck(cleaned, False, "does not match the AAAAA9999A pattern")


def normalize_aadhaar(value: str | None) -> str | None:
    """Spaces are UIDAI's own display grouping and carry no information."""
    cleaned = _strip(value)
    return cleaned or None


def check_aadhaar(value: str | None) -> IdentifierCheck:
    cleaned = normalize_aadhaar(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if not AADHAAR_RE.match(cleaned):
        return IdentifierCheck(cleaned, False, "not 12 digits starting 2-9")
    if not verhoeff_valid(cleaned):
        # Most often an OCR misread rather than a fabricated number, so the
        # wording stays neutral; the rule engine raises REVIEW, not FAIL.
        return IdentifierCheck(cleaned, False, "checksum does not validate; possible misread")
    return IdentifierCheck(cleaned, True, "well-formed Aadhaar with valid checksum")


def normalize_passport(value: str | None) -> str | None:
    cleaned = _strip(value).upper()
    return cleaned or None


def check_passport(value: str | None) -> IdentifierCheck:
    cleaned = normalize_passport(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if PASSPORT_IN_RE.match(cleaned):
        return IdentifierCheck(cleaned, True, "well-formed Indian passport number")
    # Foreign passports are legitimately different shapes, so an unrecognised
    # pattern is reported without being called invalid.
    return IdentifierCheck(cleaned, False, "does not match the Indian passport pattern")


def normalize_driving_license(value: str | None) -> str | None:
    cleaned = _strip(value).upper()
    return cleaned or None


def check_driving_license(value: str | None) -> IdentifierCheck:
    cleaned = normalize_driving_license(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if DL_IN_RE.match(cleaned):
        return IdentifierCheck(cleaned, True, "well-formed driving licence number")
    return IdentifierCheck(cleaned, False, "state driving licence formats vary; not recognised")


def normalize_account_number(value: str | None) -> str | None:
    """Leading zeros are significant in account numbers and stay put."""
    cleaned = _strip(value)
    return cleaned or None


def normalize_phone(value: str | None) -> str | None:
    """Reduce to the national subscriber number so +91-98..., 098... and
    98... compare equal."""
    cleaned = re.sub(r"[^\d+]", "", str(value or ""))
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]
    return cleaned or None


def check_phone(value: str | None) -> IdentifierCheck:
    cleaned = normalize_phone(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if PHONE_IN_RE.match(cleaned):
        return IdentifierCheck(cleaned, True, "well-formed Indian mobile number")
    return IdentifierCheck(cleaned, False, "not a 10-digit Indian mobile number")


def normalize_email(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def check_email(value: str | None) -> IdentifierCheck:
    cleaned = normalize_email(value)
    if not cleaned:
        return IdentifierCheck(None, False, "no value")
    if EMAIL_RE.match(cleaned):
        return IdentifierCheck(cleaned, True, "well-formed email address")
    return IdentifierCheck(cleaned, False, "not a valid email address")


# Dispatch used by the normaliser so callers do not branch on field names.
NORMALIZERS = {
    "pan": normalize_pan,
    "aadhaar": normalize_aadhaar,
    "passport": normalize_passport,
    "driving_license": normalize_driving_license,
    "bank_account": normalize_account_number,
    "account_number": normalize_account_number,
    "phone": normalize_phone,
    "email": normalize_email,
}

CHECKERS = {
    "pan": check_pan,
    "aadhaar": check_aadhaar,
    "passport": check_passport,
    "driving_license": check_driving_license,
    "phone": check_phone,
    "email": check_email,
}

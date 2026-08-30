"""Money and numeric parsing.

Currency arrives written a dozen ways across a document set: 5,00,000 in Indian
grouping, 500000.00, Rs. 5 Lakh, INR 5,00,000/-. All of it has to reduce to one
Decimal before any comparison, and anything that cannot be read confidently
must come back as None rather than a guess.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"(?:rs\.?|inr|₹|usd|\$|eur|€|gbp|£)", re.IGNORECASE)
# A sanction letter states a figure and then restates it in words:
# "Rs. 5,00,000/- (Rupees Five Lakh only)". The parenthetical is a legal
# convention, not part of the number. It is stripped only when it contains no
# digits of its own, so the accounting negative "(1,200.00)" is left alone.
_WORD_PARENTHETICAL = re.compile(r"\(\s*[^\d()]*\)")
_TRAILING = re.compile(r"(/-|only|/=)\s*$", re.IGNORECASE)
_NON_NUMERIC = re.compile(r"[^0-9.\-]")
_MULTIPLIERS = {
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "thousand": Decimal(1_000),
    "k": Decimal(1_000),
    "million": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
}
_WORD_AMOUNT = re.compile(
    r"([0-9][0-9,]*\.?[0-9]*)\s*(" + "|".join(_MULTIPLIERS) + r")\b", re.IGNORECASE
)


def parse_amount(raw: str | int | float | None) -> Decimal | None:
    """Best-effort money parse. None when the text is not a single amount."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))

    text = str(raw).strip()
    if not text:
        return None

    text = _CURRENCY.sub(" ", text)
    text = _WORD_PARENTHETICAL.sub(" ", text).strip()
    # The end markers stack — "5,00,000/- only" — and removing one exposes the
    # next, so this repeats. Getting it wrong is not a harmless miss: a "/-"
    # left stranded in the middle of the string survives digit-stripping as a
    # bare "-", which fails to parse, and an amount that will not parse is
    # skipped by the comparison rules without a finding.
    while (stripped := _TRAILING.sub("", text).strip()) != text:
        text = stripped

    # "5 Lakh" style before digit stripping, since the unit word carries scale.
    word_match = _WORD_AMOUNT.search(text)
    if word_match:
        base = word_match.group(1).replace(",", "")
        unit = word_match.group(2).lower()
        try:
            return (Decimal(base) * _MULTIPLIERS[unit]).normalize()
        except (InvalidOperation, KeyError):
            return None

    # Parenthesised negatives are an accounting convention, not a typo.
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _NON_NUMERIC.sub("", text.replace(",", ""))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    if cleaned.count(".") > 1:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative and value > 0 else value


_WORD_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
# Indian grouping, which is what these documents use. "Hundred" multiplies what
# came before it; the rest close a group off.
_WORD_SCALES = {
    "hundred": Decimal(100),
    "thousand": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "million": Decimal(1_000_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "billion": Decimal(1_000_000_000),
}
# Words that carry no value. "Paise" ends the rupee part, so anything after it
# is dropped rather than added to the total.
_WORD_NOISE = {"rupees", "rupee", "inr", "rs", "only", "and", "of", "the", "in",
               "words", "say"}
_WORD_TOKEN = re.compile(r"[a-z]+")


def parse_amount_words(raw: str | None) -> Decimal | None:
    """Read an amount written out in words, Indian-style.

    "Rupees Five Lakh only" is how a sanction letter states a figure, and where
    the words and the numerals disagree it is the words that prevail in law.
    Reading them is the only way to check one against the other.

    Returns None rather than a guess: an unparseable phrase, or one carrying no
    number at all, is not something to be approximated.
    """
    if raw is None:
        return None
    text = str(raw).lower()
    # The paise part is a different unit and is not added to the rupee total.
    # Cutting at the word "paise" is not enough — "five lakh and fifty paise"
    # would keep the fifty and report 500050 — so the cut is made at the "and"
    # that introduces the clause.
    paise = re.search(r"\bpais[ae]\b", text)
    if paise:
        head = text[: paise.start()]
        conjunction = list(re.finditer(r"\band\b", head))
        if conjunction:
            text = head[: conjunction[-1].start()]
        else:
            # No conjunction, as in "five lakh fifty paise": the trailing plain
            # numbers are the paise, and only a scale word ends the rupees.
            words = _WORD_TOKEN.findall(head)
            while words and words[-1] in _WORD_UNITS:
                words.pop()
            text = " ".join(words)

    tokens = [t for t in _WORD_TOKEN.findall(text) if t not in _WORD_NOISE]
    if not tokens:
        return None

    total = Decimal(0)
    current = Decimal(0)
    seen_a_number = False

    for token in tokens:
        if token in _WORD_UNITS:
            current += Decimal(_WORD_UNITS[token])
            seen_a_number = True
        elif token == "hundred":
            # Multiplies what is being built, rather than closing it.
            current = (current or Decimal(1)) * _WORD_SCALES["hundred"]
        elif token in _WORD_SCALES:
            total += (current or Decimal(1)) * _WORD_SCALES[token]
            current = Decimal(0)
            seen_a_number = True
        else:
            # An unknown word inside the phrase means this is prose, not an
            # amount, and reading a number out of it would be invention.
            return None

    if not seen_a_number:
        return None
    return (total + current).normalize()


def normalize_amount(raw: str | int | float | None) -> str | None:
    """Canonical string form used for storage and exact comparison."""
    value = parse_amount(raw)
    if value is None:
        return None
    quantised = value.quantize(Decimal("0.01"))
    # Drop a pure .00 tail so 500000 and 500000.00 store identically.
    return str(quantised.normalize()) if quantised == quantised.to_integral_value() else str(quantised)


def amounts_equal(
    a: str | int | float | None,
    b: str | int | float | None,
    *,
    tolerance_pct: float = 0.0,
    tolerance_abs: Decimal | float = 0,
) -> tuple[bool | None, str]:
    """Compare two amounts within an optional tolerance.

    Returns ``(equal, reason)``; None when either side is unreadable, which the
    rule engine turns into REVIEW rather than a mismatch.
    """
    left, right = parse_amount(a), parse_amount(b)
    if left is None or right is None:
        return None, "one or both amounts could not be parsed"
    if left == right:
        return True, "amounts match exactly"

    difference = abs(left - right)
    allowed = Decimal(str(tolerance_abs))
    if tolerance_pct:
        largest = max(abs(left), abs(right))
        allowed = max(allowed, largest * Decimal(str(tolerance_pct)) / Decimal(100))
    if difference <= allowed:
        return True, f"difference {difference} is within the configured tolerance"
    return False, f"{left} does not match {right} (difference {difference})"


def sum_amounts(values: list[str | int | float | None]) -> Decimal | None:
    """Total a column. None if any entry is unreadable, since a partial sum
    would silently understate a reconciliation check."""
    total = Decimal(0)
    for value in values:
        parsed = parse_amount(value)
        if parsed is None:
            return None
        total += parsed
    return total


def percentage_difference(a: str | int | float | None, b: str | int | float | None) -> float | None:
    left, right = parse_amount(a), parse_amount(b)
    if left is None or right is None:
        return None
    largest = max(abs(left), abs(right))
    if largest == 0:
        return 0.0
    return float(abs(left - right) / largest * 100)

"""Comparison dispatch: the right method for the kind of value being compared.

The routing is the point. An identifier goes to exact comparison because any
difference matters; a name goes to fuzzy because most differences do not; and
only what fuzzy cannot settle is ever escalated to the model. Deciding this by
field kind rather than per call site is what keeps the behaviour consistent
across the rule engine, the profile builder and the cross-document pass.
"""

from __future__ import annotations

from app.comparison.base import (
    ComparisonOutcome,
    ComparisonVerdict,
    different,
    equal,
    not_comparable,
    undetermined,
)
from app.comparison.exact import (
    compare_amount,
    compare_date,
    compare_exact_text,
    compare_identifier,
)
from app.comparison.fuzzy import (
    DEFAULT_THRESHOLDS,
    FuzzyThresholds,
    compare_address,
    compare_name,
    compare_organisation,
)

# Which comparison a canonical field gets.
FIELD_METHOD: dict[str, str] = {
    "name": "name",
    "father_name": "name",
    "mother_name": "name",
    "spouse_name": "name",
    "date_of_birth": "date",
    "pan": "identifier:pan",
    "aadhaar": "identifier:aadhaar",
    "passport": "identifier:passport",
    "driving_license": "identifier:driving_license",
    "bank_account": "identifier:bank_account",
    "phone": "identifier:phone",
    "email": "identifier:email",
    "current_address": "address",
    "permanent_address": "address",
    "employer": "organisation",
    "designation": "text",
    "income": "amount",
    "loan_amount": "amount",
    "gender": "text",
    "property_address": "address",
    "property_value": "amount",
    "survey_number": "exact",
    "property_owner_name": "name",
}


def compare_field(
    field_name: str,
    left: str | None,
    right: str | None,
    *,
    thresholds: FuzzyThresholds = DEFAULT_THRESHOLDS,
    amount_tolerance_pct: float = 0.0,
) -> ComparisonOutcome:
    """Compare two values of the same canonical field."""
    method = FIELD_METHOD.get(field_name, "text")

    if method == "name":
        return compare_name(left, right, thresholds=thresholds)
    if method == "address":
        return compare_address(left, right, thresholds=thresholds)
    if method == "organisation":
        return compare_organisation(left, right, thresholds=thresholds)
    if method == "date":
        return compare_date(left, right)
    if method == "amount":
        return compare_amount(left, right, tolerance_pct=amount_tolerance_pct)
    if method.startswith("identifier:"):
        return compare_identifier(method.split(":", 1)[1], left, right)
    return compare_exact_text(left, right)


__all__ = [
    "DEFAULT_THRESHOLDS",
    "ComparisonOutcome",
    "ComparisonVerdict",
    "FuzzyThresholds",
    "compare_address",
    "compare_amount",
    "compare_date",
    "compare_exact_text",
    "compare_field",
    "compare_identifier",
    "compare_name",
    "compare_organisation",
    "different",
    "equal",
    "not_comparable",
    "undetermined",
]

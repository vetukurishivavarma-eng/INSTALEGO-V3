"""The extraction-degradation matrix: which document, under which condition.

The nine evaluation cases in ``cases.py`` measure the system end to end and
answer "does a discrepancy get found". This matrix measures the layer beneath
them and answers a narrower question: at what point does the model stop reading
the page correctly.

They are kept apart deliberately. An end-to-end case runs the whole pipeline
and costs a dozen model calls; a variant here runs one document and costs
three, which is what makes a twenty-five-condition sweep affordable on a free
endpoint. And a single number over both would hide the thing worth knowing,
which is not "how accurate is the system" but "which specific condition breaks
it".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.degrade import CORE_DEGRADATIONS, DEGRADATIONS
from tests.fixtures.hard_documents import CORE_DOCUMENTS, DOCUMENTS, build_variant


@dataclass(frozen=True)
class Variant:
    """One document rendered under one condition: the unit of measurement."""

    doc_id: str
    degradation: str
    fmt: str = "jpg"

    @property
    def variant_id(self) -> str:
        return f"{self.doc_id}/{self.degradation}"

    @property
    def document(self):
        return DOCUMENTS[self.doc_id]

    @property
    def condition(self):
        return DEGRADATIONS[self.degradation]

    def build(self, directory: str | Path) -> Path:
        return build_variant(self.doc_id, self.degradation, directory, fmt=self.fmt)


def matrix(
    doc_ids: tuple[str, ...] | None = None,
    degradations: tuple[str, ...] | None = None,
    fmt: str = "jpg",
) -> list[Variant]:
    """Every combination of the selected documents and conditions."""
    return [
        Variant(doc_id, degradation, fmt)
        for doc_id in (doc_ids or tuple(DOCUMENTS))
        for degradation in (degradations or tuple(DEGRADATIONS))
    ]


def core_matrix(fmt: str = "jpg") -> list[Variant]:
    """The affordable sweep: three layouts against nine conditions.

    Twenty-seven variants at roughly three model calls each fits inside a free
    tier's daily allowance with room to repeat a few. The full matrix is 125
    variants and needs either a paid endpoint or several days.
    """
    return matrix(CORE_DOCUMENTS, CORE_DEGRADATIONS, fmt)


# --------------------------------------------------------------------------
# How each field is compared
# --------------------------------------------------------------------------
# Fields that are not on the canonical applicant profile fall back to "text"
# normalisation, which compares "42,000.00" and "42000" as different strings.
# For anything holding money or a date that is the wrong question, so the kind
# is stated here rather than inferred.
SCORING_KINDS: dict[str, str] = {
    "net_salary": "amount",
    "deductions": "amount",
    "closing_balance": "amount",
    "agreement_value": "amount",
    "agreement_date": "date",
    "document_date": "date",
    "date_of_issue": "date",
    "date_of_expiry": "date",
}

# Free-text fields where an exact match is too strict to be informative: an
# address read as "12 MG Rd" rather than "12, M.G. Road" is a correct read, and
# scoring it as a failure would bury the real ones.
SIMILARITY_FIELDS = {"current_address", "permanent_address", "employer", "bank_name",
                     "designation", "party_one", "party_two", "subject"}
SIMILARITY_THRESHOLD = 0.85

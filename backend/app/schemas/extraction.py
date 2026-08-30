"""Contracts for the classification and extraction agents.

These are the schemas the model is held to. They are strict on purpose: a
missing confidence, an invented document type or a field without a source is a
validation failure that triggers a correction turn, not something that quietly
flows downstream into a report.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DocumentType

# Sentinels the extractor must use instead of guessing or omitting a field.
NOT_FOUND = "NOT_FOUND"
UNCERTAIN = "UNCERTAIN"


class SourceRef(BaseModel):
    """Where in the document a value was read."""

    model_config = ConfigDict(extra="ignore")

    page: int = Field(default=0, ge=0)
    text: str = Field(default="", description="the quoted span the value came from")
    bbox: list[float] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def _bbox_shape(cls, value: list[float]) -> list[float]:
        # Either a full rectangle or nothing; a partial box would draw a
        # highlight in the wrong place in the viewer.
        if value and len(value) != 4:
            return []
        return value


class RegisteredTransaction(BaseModel):
    """One row of an encumbrance certificate's table.

    An EC is a ledger, not a statement: it lists every registered transaction
    affecting a property over a period. Read as a list it can establish a chain
    of title on its own, from a single document, which is what the deeds
    otherwise have to be gathered to do.
    """

    model_config = ConfigDict(extra="ignore")

    serial: str = ""
    date: str = ""
    # The nature of the entry: sale, mortgage, gift, release, partition.
    nature: str = ""
    # Whose interest left, and whose it became. Named as the certificate names
    # them, which is by role rather than by "seller" and "buyer": a mortgage
    # has an executant and a claimant too.
    executant: str = ""
    claimant: str = ""
    document_number: str = ""
    extent: str = ""

    @property
    def is_transfer(self) -> bool:
        """Whether this row moves ownership, as opposed to encumbering it.

        A mortgage and its release both appear in the same table as a sale, and
        only a sale changes who owns the land. Treating a mortgage as a link
        would break every chain that ever carried a loan.
        """
        wording = self.nature.lower()
        if any(word in wording for word in ("mortgage", "lien", "charge", "lease",
                                            "attachment", "release", "agreement")):
            return False
        return any(word in wording for word in ("sale", "conveyance", "transfer", "gift",
                                                "settlement", "partition", "deed"))


class EncumbranceLedger(BaseModel):
    """Everything an encumbrance certificate records, as a list."""

    model_config = ConfigDict(extra="ignore")

    property_description: str = ""
    period_from: str = ""
    period_to: str = ""
    transactions: list[RegisteredTransaction] = Field(default_factory=list)
    # The certificate's own summary, where it makes one ("NIL").
    summary: str = ""
    notes: str = ""


class ClassificationResult(BaseModel):
    """Output of the document classifier."""

    model_config = ConfigDict(extra="ignore")

    document_type: DocumentType = DocumentType.UNKNOWN
    subtype: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_readable: bool = True
    reason: str = ""
    pages_relevant: list[int] = Field(default_factory=list)

    @field_validator("document_type", mode="before")
    @classmethod
    def _unknown_for_unrecognised(cls, value: Any) -> Any:
        """A type outside the vocabulary becomes UNKNOWN rather than an error.

        The instruction to the model is not to guess; if it invents a label
        anyway, the safe reading is that classification did not succeed.
        """
        if value is None:
            return DocumentType.UNKNOWN
        if isinstance(value, str):
            candidate = value.strip().upper().replace(" ", "_").replace("-", "_")
            if candidate in DocumentType.__members__:
                return candidate
            return DocumentType.UNKNOWN
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        # Some models emit percentages.
        if number > 1.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))


class ExtractedField(BaseModel):
    """One field read from one document, with the evidence for it."""

    model_config = ConfigDict(extra="ignore")

    field: str
    value: str = NOT_FOUND
    normalized_value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: SourceRef = Field(default_factory=SourceRef)

    @field_validator("value", "normalized_value", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> str:
        """Numbers and dates arrive typed; store them as written text.

        Coercing to string here keeps leading zeros and preserves the exact
        characters on the page, which a float would destroy.
        """
        if value is None:
            return NOT_FOUND
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip()

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))

    @property
    def is_present(self) -> bool:
        return self.value not in {NOT_FOUND, UNCERTAIN, ""}

    @property
    def is_uncertain(self) -> bool:
        return self.value == UNCERTAIN


class ExtractionResult(BaseModel):
    """Output of the extraction agent for a single document."""

    model_config = ConfigDict(extra="ignore")

    document_type: str = ""
    fields: list[ExtractedField] = Field(default_factory=list)
    notes: str = ""

    def by_name(self, name: str) -> ExtractedField | None:
        for item in self.fields:
            if item.field == name:
                return item
        return None

    def present_fields(self) -> list[ExtractedField]:
        return [f for f in self.fields if f.is_present]

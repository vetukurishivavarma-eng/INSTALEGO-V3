"""The canonical applicant profile.

One consolidated view of a person, built only from what the documents said.
The important property is that a conflict is preserved rather than resolved:
when two documents disagree, both values survive on the field with their
sources attached, the status becomes CONFLICTING, and the rule engine decides
what that means. Nothing here picks a winner.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import FieldStatus

# The fields a profile can carry. Ordered as a reviewer reads them: who the
# person is, how to reach them, then what they earn and are asking for.
CANONICAL_FIELDS: tuple[str, ...] = (
    "name",
    "date_of_birth",
    "gender",
    "father_name",
    "mother_name",
    "spouse_name",
    "pan",
    "aadhaar",
    "passport",
    "driving_license",
    "phone",
    "email",
    "current_address",
    "permanent_address",
    "employer",
    "designation",
    "income",
    "bank_account",
    "loan_amount",
    # Land title. property_details used to carry all three of these, so an
    # address and a price landed on one field and the profile builder saw them
    # as two documents disagreeing about a single value.
    "property_address",
    "property_value",
    "survey_number",
    "property_owner_name",
)

# Which normaliser applies to which field, used by the Python normalisation
# pass. Anything absent here is treated as free text.
FIELD_KINDS: dict[str, str] = {
    "name": "name",
    "father_name": "name",
    "mother_name": "name",
    "spouse_name": "name",
    "date_of_birth": "date",
    "pan": "pan",
    "aadhaar": "aadhaar",
    "passport": "passport",
    "driving_license": "driving_license",
    "phone": "phone",
    "email": "email",
    "bank_account": "bank_account",
    "income": "amount",
    "loan_amount": "amount",
    "current_address": "address",
    "permanent_address": "address",
    "property_address": "address",
    "property_value": "amount",
    "property_owner_name": "name",
}


class ProfileSource(BaseModel):
    """A document that asserted a particular value for a field."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = ""
    document_name: str = ""
    document_type: str = ""
    page: int = 0
    value: str = ""
    normalized_value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    snippet: str = ""
    bbox: list[float] = Field(default_factory=list)


class ProfileField(BaseModel):
    """One canonical field: the agreed value, or the disagreement."""

    model_config = ConfigDict(extra="ignore")

    value: str = ""
    normalized_value: str = ""
    status: FieldStatus = FieldStatus.NOT_FOUND
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[ProfileSource] = Field(default_factory=list)
    # Every distinct value seen, kept even when one of them is presented as
    # the headline value. This is what a reviewer needs to see a conflict.
    candidates: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if candidate in FieldStatus.__members__:
                return candidate
            return FieldStatus.UNCERTAIN
        return value

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
    def is_conflicting(self) -> bool:
        return self.status == FieldStatus.CONFLICTING

    @property
    def is_present(self) -> bool:
        return self.status in {FieldStatus.CONFIRMED, FieldStatus.CONFLICTING, FieldStatus.UNCERTAIN}


class ApplicantProfileSchema(BaseModel):
    """The whole profile, keyed by canonical field name."""

    model_config = ConfigDict(extra="ignore")

    fields: dict[str, ProfileField] = Field(default_factory=dict)

    def get(self, name: str) -> ProfileField | None:
        return self.fields.get(name)

    def value_of(self, name: str) -> str | None:
        field = self.fields.get(name)
        if field is None or not field.is_present:
            return None
        return field.value or None

    def conflicting_fields(self) -> list[str]:
        return [name for name, field in self.fields.items() if field.is_conflicting]

    def present_field_names(self) -> list[str]:
        return [name for name, field in self.fields.items() if field.is_present]


class ProfileBuilderResult(BaseModel):
    """What the profile-building agent returns.

    A flat mapping of field name to ProfileField, which the service converts
    into ApplicantProfileSchema after checking the agent did not introduce a
    field no document mentioned.
    """

    model_config = ConfigDict(extra="allow")

    @classmethod
    def to_profile(cls, payload: dict[str, Any]) -> ApplicantProfileSchema:
        fields: dict[str, ProfileField] = {}
        for name, raw in (payload or {}).items():
            if name not in CANONICAL_FIELDS or not isinstance(raw, dict):
                continue
            fields[name] = ProfileField.model_validate(raw)
        return ApplicantProfileSchema(fields=fields)

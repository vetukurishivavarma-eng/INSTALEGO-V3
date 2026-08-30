"""The canonical applicant profile: one consolidated view per case."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.case import Case


class ApplicantProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "applicant_profiles"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # {field_name: {value, status, confidence, sources: [...], candidates: [...]}}
    # Conflicting values are preserved under `candidates` rather than resolved.
    fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    case: Mapped["Case"] = relationship(back_populates="profile")

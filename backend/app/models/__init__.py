"""SQLAlchemy models. Importing this package registers every mapper."""

from app.models.applicant import ApplicantProfile
from app.models.audit import AuditLog
from app.models.case import Case
from app.models.discrepancy import Discrepancy
from app.models.document import Document, DocumentPage
from app.models.evidence import Evidence
from app.models.extraction import Extraction, FieldValue
from app.models.report import Report
from app.models.validation import ValidationResult

__all__ = [
    "ApplicantProfile",
    "AuditLog",
    "Case",
    "Discrepancy",
    "Document",
    "DocumentPage",
    "Evidence",
    "Extraction",
    "FieldValue",
    "Report",
    "ValidationResult",
]

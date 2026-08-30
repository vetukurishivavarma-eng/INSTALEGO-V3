"""Targeted field extraction.

The model is never asked to "extract everything". What it is asked for depends
entirely on what the document turned out to be: an Aadhaar card is asked for
five fields, a bank statement for six different ones. That single decision is
most of the token budget and most of the accuracy — a narrow question about a
known document type gets a precise answer, while an open one invites the model
to fill the page.

Page selection is the other half. The classifier reports which pages carried
the evidence, and only those pages are sent.
"""

from __future__ import annotations

import logging

from app.agents.base_agent import TEMPERATURE_EXTRACTION, AgentRun, BaseAgent, clip, render
from app.extraction.base import ParsedDocument, ParsedPage
from app.llm.client import ImageContent
from app.models.enums import DocumentType
from app.schemas.extraction import ClassificationResult, ExtractionResult

logger = logging.getLogger(__name__)

MAX_CHARS_PER_EXTRACTION = 12000
MAX_IMAGES_PER_EXTRACTION = 3
MAX_PAGES_PER_EXTRACTION = 8

# What each document type is actually asked for. Anything not listed falls back
# to GENERIC_FIELDS, which is the identity core only: an unrecognised document
# should not be mined speculatively for financial values.
REQUIRED_FIELDS: dict[str, list[str]] = {
    DocumentType.AADHAAR: [
        "name",
        "date_of_birth",
        "gender",
        "aadhaar_number",
        "address",
    ],
    DocumentType.PAN: [
        "name",
        "pan_number",
        "date_of_birth",
        "father_name",
    ],
    DocumentType.PASSPORT: [
        "name",
        "passport_number",
        "date_of_birth",
        "gender",
        "date_of_issue",
        "date_of_expiry",
        "address",
    ],
    DocumentType.DRIVING_LICENSE: [
        "name",
        "driving_license_number",
        "date_of_birth",
        "address",
        "date_of_issue",
        "date_of_expiry",
    ],
    DocumentType.BANK_STATEMENT: [
        "account_holder_name",
        "account_number",
        "bank_name",
        "statement_period",
        "closing_balance",
        "address",
    ],
    DocumentType.SALARY_SLIP: [
        "employee_name",
        "employer",
        "designation",
        "salary_period",
        "gross_salary",
        "net_salary",
        "deductions",
    ],
    DocumentType.LOAN_APPLICATION: [
        "applicant_name",
        "date_of_birth",
        "address",
        "permanent_address",
        "phone",
        "email",
        "pan_number",
        "aadhaar_number",
        "loan_amount",
        "employer",
        "designation",
        "income",
    ],
    DocumentType.ITR: [
        "name",
        "pan_number",
        "assessment_year",
        "gross_total_income",
        "total_tax_paid",
        "filing_date",
    ],
    DocumentType.EMPLOYMENT_PROOF: [
        "employee_name",
        "employer",
        "designation",
        "date_of_joining",
        "income",
    ],
    DocumentType.ADDRESS_PROOF: [
        "name",
        "address",
        "document_date",
    ],
    DocumentType.IDENTITY_PROOF: [
        "name",
        "date_of_birth",
        "identifier_number",
        "address",
        "date_of_expiry",
    ],
    DocumentType.PROPERTY_DOCUMENT: [
        "owner_name",
        "property_address",
        "property_value",
        "document_date",
        "registration_number",
    ],
    DocumentType.FINANCIAL_STATEMENT: [
        "name",
        "period",
        "total_income",
        "total_expenses",
        "net_position",
    ],
    DocumentType.TAX_DOCUMENT: [
        "name",
        "pan_number",
        "assessment_year",
        "tax_amount",
    ],
    DocumentType.AGREEMENT: [
        "party_one",
        "party_two",
        "agreement_date",
        "agreement_value",
        "subject",
        # A sanction letter is an agreement, and the figure it sanctions is the
        # one worth comparing against the amount the applicant asked for.
        "loan_amount",
    ],
    DocumentType.LEGAL_DOCUMENT: [
        "party_one",
        "party_two",
        "document_date",
        "subject",
    ],
}

GENERIC_FIELDS: list[str] = ["name", "date_of_birth", "address", "document_date"]

# Extracted field name -> canonical profile field. Documents label the same
# thing a dozen ways; the mapping lives here so the profile builder receives a
# consistent vocabulary and never has to guess that "employee_name" is a name.
CANONICAL_MAP: dict[str, str] = {
    "name": "name",
    "applicant_name": "name",
    "account_holder_name": "name",
    "employee_name": "name",
    "owner_name": "name",
    "father_name": "father_name",
    "mother_name": "mother_name",
    "spouse_name": "spouse_name",
    "date_of_birth": "date_of_birth",
    "gender": "gender",
    "pan_number": "pan",
    "aadhaar_number": "aadhaar",
    "passport_number": "passport",
    "driving_license_number": "driving_license",
    "phone": "phone",
    "email": "email",
    "address": "current_address",
    "permanent_address": "permanent_address",
    "property_address": "property_details",
    "employer": "employer",
    "designation": "designation",
    "income": "income",
    "gross_salary": "income",
    "gross_total_income": "income",
    "total_income": "income",
    "net_salary": "net_salary",
    "account_number": "bank_account",
    "loan_amount": "loan_amount",
    "property_value": "property_details",
}


# Where a field name alone does not say where the value stops. A letter prints
# the addressee as a name on one line and their address on the next, and asking
# for "party_two" against that block is an underspecified question — the model
# returned both, which was a fair reading of what was asked.
#
# Only fields with a demonstrated ambiguity are described. A description is a
# change to what is being asked for, so each one costs a re-measurement; adding
# them speculatively to fields that already extract cleanly risks moving
# something that works.
FIELD_DESCRIPTIONS: dict[str, str] = {
    "party_one": (
        "the name of the first party only — a person or an organisation, "
        "without their address, title or role in the agreement"
    ),
    "party_two": (
        "the name of the second party only — for a sanction or offer letter "
        "this is the addressee, and it is their name alone, not the address "
        "block printed beneath it"
    ),
    "name": "the person's name only, without any address, title or salutation",
    "applicant_name": "the applicant's name only, without any address or title",
    "account_holder_name": "the account holder's name only, without the address",
    "employee_name": "the employee's name only, without a designation or code",
    "owner_name": "the owner's name only, without the property address",
    "address": "the full postal address, without the name of the person it belongs to",
    "permanent_address": "the full permanent address, without the name it belongs to",
}


def required_fields_for(document_type: str) -> list[str]:
    return REQUIRED_FIELDS.get(document_type, GENERIC_FIELDS)


def describe_fields(names: list[str]) -> str:
    """The field list as the prompt sees it, with definitions where they exist."""
    lines = []
    for name in names:
        description = FIELD_DESCRIPTIONS.get(name)
        lines.append(f"- {name}: {description}" if description else f"- {name}")
    return "\n".join(lines)


def canonical_field_for(extracted_name: str) -> str | None:
    """The profile field an extracted name maps to, or None if it is
    document-local (statement period, deductions, and so on)."""
    return CANONICAL_MAP.get(extracted_name)


class DocumentExtractorAgent(BaseAgent):
    prompt_name = "extractor"
    temperature = TEMPERATURE_EXTRACTION

    def extract(
        self,
        parsed: ParsedDocument,
        classification: ClassificationResult,
        *,
        fields: list[str] | None = None,
    ) -> AgentRun[ExtractionResult]:
        document_type = str(classification.document_type)
        required = fields or required_fields_for(document_type)
        pages = self._select_pages(parsed, classification)

        text = clip(self._page_text(pages), MAX_CHARS_PER_EXTRACTION)
        images = [
            ImageContent(data=page.image_bytes, media_type=page.image_media_type)
            for page in pages
            if page.image_bytes
        ][:MAX_IMAGES_PER_EXTRACTION]

        if not text and not images:
            return AgentRun(
                data=ExtractionResult(
                    document_type=document_type,
                    notes="no readable content was available to extract from",
                ),
                model="none",
                prompt_version=self.version,
                attempts=0,
            )

        system = render(
            self.system_prompt,
            {
                "document_type": document_type,
                "required_fields": describe_fields(required),
            },
        )
        prompt = (
            "Extract the required fields from this document.\n"
            f"Pages supplied: {', '.join(str(p.page_number) for p in pages)}.\n"
            "Return one entry per required field, including the ones you cannot find."
        )

        run = self._run(
            ExtractionResult,
            prompt=prompt,
            system=system,
            text=text or None,
            images=images or None,
        )
        run.data = self._drop_unrequested(run.data, required, document_type)
        return run

    @staticmethod
    def _drop_unrequested(
        result: ExtractionResult, required: list[str], document_type: str
    ) -> ExtractionResult:
        """Keep only fields that were asked for.

        A model that volunteers extra fields is a model that has started
        inferring, and an unrequested value has no place in the audit chain.
        """
        allowed = set(required)
        kept = [item for item in result.fields if item.field in allowed]
        dropped = len(result.fields) - len(kept)
        if dropped:
            logger.info("dropped %d unrequested field(s) from the extraction", dropped)
        return result.model_copy(update={"fields": kept, "document_type": document_type})

    @staticmethod
    def _select_pages(
        parsed: ParsedDocument, classification: ClassificationResult
    ) -> list[ParsedPage]:
        """Only the pages the classifier said carried evidence."""
        by_number = {page.page_number: page for page in parsed.pages}
        chosen = [by_number[n] for n in classification.pages_relevant if n in by_number]

        if not chosen:
            chosen = list(parsed.pages)

        # A page with neither text nor a render contributes nothing but tokens.
        chosen = [page for page in chosen if page.text or page.image_bytes]
        return chosen[:MAX_PAGES_PER_EXTRACTION]

    @staticmethod
    def _page_text(pages: list[ParsedPage]) -> str:
        parts = []
        for page in pages:
            if not page.text:
                continue
            label = page.label or f"Page {page.page_number}"
            parts.append(f"--- {label} (page {page.page_number}) ---\n{page.text}")
        return "\n\n".join(parts)

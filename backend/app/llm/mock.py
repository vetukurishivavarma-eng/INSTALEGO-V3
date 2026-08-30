"""A deterministic stand-in for the model, for development without a GPU.

This exists so the pipeline can be run, demonstrated and integration-tested on
a laptop with no vLLM server attached. It is not a model and does not pretend
to be one: classification is keyword matching, extraction is a regular
expression over label/value lines, and every judgement call returns the
cautious answer.

What that buys is real coverage of everything around the model — parsing,
normalisation, the rule engine, comparison, report rendering, the API and the
UI all run against genuine data. What it cannot tell you is whether the real
model extracts correctly; that is what the evaluation suite against a live
endpoint is for.

Refuses to start in production, because a stubbed extraction reaching a bank
report would be far worse than an outage.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import settings
from app.llm.client import (
    BaseLLMClient,
    ImageContent,
    LLMResponse,
    Message,
    StructuredResult,
)

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# Keyword signatures used to guess a document type from its text.
_TYPE_SIGNATURES: list[tuple[str, tuple[str, ...]]] = [
    ("AADHAAR", ("aadhaar", "uidai", "unique identification")),
    ("PAN", ("permanent account number", "income tax department", "pan")),
    ("PASSPORT", ("passport", "republic of india passport")),
    ("DRIVING_LICENSE", ("driving licence", "driving license", "transport department")),
    ("BANK_STATEMENT", ("statement of account", "bank statement", "closing balance")),
    ("SALARY_SLIP", ("salary slip", "payslip", "pay slip", "net salary", "gross salary")),
    ("ITR", ("income tax return", "itr", "assessment year")),
    ("LOAN_APPLICATION", ("loan application", "loan amount", "credit assessment")),
    ("EMPLOYMENT_PROOF", ("employment", "appointment letter", "date of joining")),
    ("PROPERTY_DOCUMENT", ("sale deed", "property", "registration number")),
    ("AGREEMENT", ("agreement", "party of the first part")),
    ("ADDRESS_PROOF", ("electricity bill", "utility bill", "address proof")),
]

# Field name -> the labels a document might print for it.
_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("name", "applicant name", "account holder name", "employee name", "owner name"),
    "applicant_name": ("applicant name", "name"),
    "account_holder_name": ("account holder name", "account holder", "name"),
    "employee_name": ("employee name", "name"),
    "owner_name": ("owner name", "name"),
    "date_of_birth": ("date of birth", "dob", "birth date"),
    "gender": ("gender", "sex"),
    "father_name": ("father name", "fathers name", "father"),
    "mother_name": ("mother name", "mothers name", "mother"),
    "spouse_name": ("spouse name", "spouse"),
    "aadhaar_number": ("aadhaar", "aadhaar number", "uid"),
    "pan_number": ("pan", "pan number", "permanent account number"),
    "passport_number": ("passport", "passport number"),
    "driving_license_number": ("driving licence", "driving license", "dl number"),
    "address": ("address", "residential address", "current address"),
    "permanent_address": ("permanent address"),
    "phone": ("phone", "mobile", "contact number"),
    "email": ("email", "e-mail"),
    "employer": ("employer", "company", "organisation", "organization"),
    "designation": ("designation", "position", "title"),
    "income": ("income", "monthly income", "annual income", "gross total income"),
    "gross_salary": ("gross salary", "gross pay"),
    "net_salary": ("net salary", "net pay", "take home"),
    "deductions": ("deductions", "total deductions"),
    "salary_period": ("salary period", "pay period", "month"),
    "statement_period": ("statement period", "period"),
    "account_number": ("account number", "a/c no", "account no"),
    "bank_name": ("bank name", "bank"),
    "closing_balance": ("closing balance", "balance"),
    "loan_amount": ("loan amount", "amount applied", "sanction amount"),
    "date_of_issue": ("date of issue", "issue date"),
    "date_of_expiry": ("date of expiry", "expiry date", "valid until", "valid upto"),
    "document_date": ("date", "document date", "issued on"),
    "assessment_year": ("assessment year",),
    "property_address": ("property address",),
    "property_value": ("property value", "consideration"),
    "registration_number": ("registration number", "registration no"),
}

# Documents present label/value pairs three ways: with a colon in prose, with
# a dash, and as two cells of a table, which the parsers render as "a | b".
_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /.'-]{1,40}?)\s*[:|\-]\s*(.+?)\s*$", re.MULTILINE)
# The stub reads its instructions out of the real prompt, so it is coupled to
# how the extractor renders its field list. A field may be listed bare or
# followed by a definition of where its value stops ("- party_two: the name of
# the second party only, ..."), and both forms must be recognised or the stub
# silently extracts less than it was asked for. Exported so the contract can be
# tested against the renderer rather than restated in a second place.
REQUESTED_FIELD = re.compile(r"^-\s*([a-z_]+)\s*(?::.*)?$", re.MULTILINE)


class MockLLMClient(BaseLLMClient):
    """Deterministic stub. Development and tests only."""

    def __init__(self, model: str | None = None) -> None:
        if settings.is_production:
            raise RuntimeError(
                "the mock LLM client cannot be used in production; "
                "set LLM_USE_MOCK=false and configure LLM_BASE_URL"
            )
        self.model = f"mock:{model or settings.LLM_MODEL}"
        logger.warning(
            "using the mock LLM client: extraction and reasoning are stubbed, "
            "only the deterministic layers are exercised"
        )

    # ------------------------------------------------------------- interface
    def generate(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(text="", model=self.model, prompt_tokens=0, completion_tokens=0)

    def analyze_image(
        self,
        image: bytes | ImageContent,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        # No pixels are read. Saying so plainly is better than returning
        # invented text that would look like a successful transcription.
        return "[MOCK] no vision model is attached; this page was not transcribed"

    def generate_structured(
        self,
        messages: list[Message],
        schema: type[TModel],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult[TModel]:
        text = "\n".join(m.content for m in messages)
        payload = self._payload_for(schema.__name__, text)
        return StructuredResult(
            data=schema.model_validate(payload),
            raw_text="",
            model=self.model,
            attempts=1,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )

    def analyze_document(
        self,
        schema: type[TModel],
        prompt: str,
        *,
        system: str | None = None,
        text: str | None = None,
        images: list[ImageContent] | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult[TModel]:
        messages = [
            Message(role="system", content=system or ""),
            Message(role="user", content=f"{prompt}\n\nDOCUMENT TEXT:\n{text or ''}"),
        ]
        return self.generate_structured(messages, schema)

    # -------------------------------------------------------------- payloads
    @staticmethod
    def _document_body(text: str) -> str:
        """Only what came from the document.

        The system prompt lists every document type by name, so classifying
        against the whole conversation matches the first keyword every time.
        A real model can tell instructions from content; this one is told.
        """
        return text.split("DOCUMENT TEXT:", 1)[-1] if "DOCUMENT TEXT:" in text else ""

    def _payload_for(self, schema_name: str, text: str) -> dict[str, Any]:
        if schema_name == "ClassificationResult":
            return self._classify(self._document_body(text))

        handler = {
            "ClassificationResult": self._classify,
            "ExtractionResult": self._extract,
            "DiscrepancyAssessment": self._assess,
            "VerificationResult": self._verify,
            "QAResult": self._qa,
        }.get(schema_name)
        if handler is None:
            return {}
        return handler(text)

    @staticmethod
    def _classify(text: str) -> dict[str, Any]:
        """Score every type, and let the most specific evidence win.

        First-match-wins is wrong here: a loan application form has a row
        labelled PAN, which would make it a PAN card. Scoring by the length of
        the matched phrase means "loan application" outweighs a bare "pan".
        """
        lowered = text.lower()
        best_type, best_score, matched = "UNKNOWN", 0, []

        for document_type, needles in _TYPE_SIGNATURES:
            hits = [n for n in needles if re.search(rf"\b{re.escape(n)}\b", lowered)]
            score = sum(len(n) for n in hits)
            if score > best_score:
                best_type, best_score, matched = document_type, score, hits

        if best_score:
            pages = sorted({int(n) for n in re.findall(r"\(page (\d+)\)", text)}) or [1]
            return {
                "document_type": best_type,
                "subtype": "",
                "confidence": 0.9,
                "is_readable": True,
                "reason": f"[MOCK] matched {', '.join(matched)}",
                "pages_relevant": pages,
            }

        return {
            "document_type": "UNKNOWN",
            "subtype": "",
            "confidence": 0.0,
            "is_readable": bool(text.strip()),
            "reason": "[MOCK] no keyword signature matched",
            "pages_relevant": [],
        }

    @staticmethod
    def _extract(text: str) -> dict[str, Any]:
        requested = REQUESTED_FIELD.findall(text)
        document_body = text.split("DOCUMENT TEXT:", 1)[-1]
        pairs = {
            label.strip().lower().replace(".", ""): (value.strip(), _page_of(document_body, value))
            for label, value in _LINE.findall(document_body)
        }

        fields = []
        for name in requested:
            labels = _FIELD_LABELS.get(name, (name.replace("_", " "),))
            found = None
            for label in labels:
                if label in pairs:
                    found = pairs[label]
                    break
            if found is None:
                fields.append(
                    {"field": name, "value": "NOT_FOUND", "normalized_value": "",
                     "confidence": 0.0, "source": {"page": 0, "text": "", "bbox": []}}
                )
            else:
                value, page = found
                fields.append(
                    {
                        "field": name,
                        "value": value,
                        "normalized_value": "",
                        "confidence": 0.9,
                        "source": {"page": page, "text": value, "bbox": []},
                    }
                )
        return {"document_type": "", "fields": fields, "notes": "[MOCK] label/value scan"}

    @staticmethod
    def _assess(text: str) -> dict[str, Any]:
        # Never resolves an ambiguity in either direction: an unattended stub
        # must not be the thing that clears or confirms a finding.
        return {
            "classification": "UNCERTAIN",
            "severity": "MEDIUM",
            "confidence": 0.0,
            "explanation": "[MOCK] no model is attached, so this candidate was not assessed",
            "evidence": [],
            "recommended_action": "Manual verification required.",
        }

    @staticmethod
    def _verify(text: str) -> dict[str, Any]:
        return {
            "verified": False,
            "confidence": 0.0,
            "corrected_values": [],
            "evidence_quality": "LOW",
            "reason": "[MOCK] no model is attached, so the evidence was not re-read",
            "final_recommendation": "MANUAL_REVIEW",
        }

    @staticmethod
    def _qa(text: str) -> dict[str, Any]:
        return {
            "passed": True,
            "errors": [],
            "requires_regeneration": False,
        }


def _page_of(body: str, value: str) -> int:
    """Recover the page number from the page headers the extractor emits."""
    index = body.find(value)
    if index < 0:
        return 1
    headers = re.findall(r"\(page (\d+)\)", body[:index])
    return int(headers[-1]) if headers else 1

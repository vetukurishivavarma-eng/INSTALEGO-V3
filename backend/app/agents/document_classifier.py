"""Document classification.

One model call per document, not per page. The classifier is shown enough to
recognise what it is looking at — the first pages, which is where letterheads,
titles and identity panels live — and nothing more. Sending all 100 pages of a
property file to answer "what kind of document is this" is exactly the waste
the pipeline is designed to avoid.

Extraction happens later and separately. This agent is forbidden from reading
applicant values, because a classifier that starts extracting is a classifier
that starts guessing.
"""

from __future__ import annotations

import logging

from app.agents.base_agent import TEMPERATURE_CLASSIFICATION, AgentRun, BaseAgent, clip
from app.extraction.base import ParsedDocument
from app.llm.client import ImageContent
from app.models.enums import DocumentType
from app.schemas.extraction import ClassificationResult

logger = logging.getLogger(__name__)

# Below this, the type is recorded but treated as unsettled.
#
# The label is a model output and is not reproducible: the same sanction letter
# came back AGREEMENT on one run and LOAN_APPLICATION on the next, from
# identical pixels and an unchanged prompt, at temperature 0. Nothing can make
# that deterministic. What can be fixed is the silence — a borderline call now
# says it was borderline, so a reviewer sees the one decision that quietly
# selects which fields were even asked for.
LOW_CLASSIFICATION_CONFIDENCE = 0.70

# How much of the document the classifier is allowed to see.
MAX_PAGES_FOR_CLASSIFICATION = 3
MAX_CHARS_FOR_CLASSIFICATION = 4000
MAX_IMAGES_FOR_CLASSIFICATION = 2

# Below this the classification is not trusted downstream: the document is
# treated as UNKNOWN so a required-document check cannot be satisfied by a
# guess. The extractor still runs, using a generic field set.
MIN_TRUSTED_CONFIDENCE = 0.55


class DocumentClassifierAgent(BaseAgent):
    prompt_name = "classifier"
    temperature = TEMPERATURE_CLASSIFICATION

    def classify(
        self, parsed: ParsedDocument, *, filename: str | None = None
    ) -> AgentRun[ClassificationResult]:
        text, images = self._select_evidence(parsed)

        if not text and not images:
            # Nothing legible reached the model, so there is nothing to
            # classify. Saying UNKNOWN here is the honest answer, and it costs
            # nothing compared to a confident label drawn from a blank page.
            return AgentRun(
                data=ClassificationResult(
                    document_type=DocumentType.UNKNOWN,
                    confidence=0.0,
                    is_readable=False,
                    reason="no readable text or usable page image was produced by parsing",
                ),
                model="none",
                prompt_version=self.version,
                attempts=0,
            )

        hint = f"FILENAME: {filename}\n" if filename else ""
        prompt = (
            f"{hint}"
            f"PAGE COUNT: {parsed.page_count}\n"
            f"FORMAT: {parsed.source_format}\n\n"
            "Classify this document. Report which pages carry the evidence you used "
            "in pages_relevant, using 1-based page numbers."
        )

        run = self._run(
            ClassificationResult,
            prompt=prompt,
            text=text or None,
            images=images or None,
        )

        result = run.data
        if result.confidence < MIN_TRUSTED_CONFIDENCE and result.document_type != DocumentType.UNKNOWN:
            logger.info(
                "classification confidence %.2f below threshold; treating as UNKNOWN",
                result.confidence,
            )
            run.data = result.model_copy(
                update={
                    "document_type": DocumentType.UNKNOWN,
                    "reason": (
                        f"{result.reason} (reported {result.document_type} at confidence "
                        f"{result.confidence:.2f}, below the {MIN_TRUSTED_CONFIDENCE:.2f} "
                        "threshold for a trusted classification)"
                    ),
                }
            )
        return run

    @staticmethod
    def _select_evidence(parsed: ParsedDocument) -> tuple[str, list[ImageContent]]:
        """The first few pages, as text where possible and pixels where not."""
        text_parts: list[str] = []
        images: list[ImageContent] = []

        for page in parsed.pages[:MAX_PAGES_FOR_CLASSIFICATION]:
            if page.text:
                label = page.label or f"Page {page.page_number}"
                text_parts.append(f"--- {label} ---\n{page.text}")
            elif page.image_bytes and len(images) < MAX_IMAGES_FOR_CLASSIFICATION:
                images.append(
                    ImageContent(data=page.image_bytes, media_type=page.image_media_type)
                )

        return clip("\n\n".join(text_parts), MAX_CHARS_FOR_CLASSIFICATION), images

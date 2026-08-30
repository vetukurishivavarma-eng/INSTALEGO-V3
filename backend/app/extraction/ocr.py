"""Turning page images into text.

Two engines, one interface. Tesseract is used when it is installed, because a
local OCR pass is cheap and gives a per-word confidence the vision model does
not. Otherwise the vision model transcribes the page, which handles the
photographed and skewed documents that defeat classical OCR.

Either way the output is text plus a confidence, and low confidence propagates
as a quality flag rather than being smoothed over.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

# Below this, the page is marked LOW_OCR_CONFIDENCE and anything extracted from
# it is treated as uncertain by the rule engine.
LOW_CONFIDENCE_THRESHOLD = 0.60

TRANSCRIPTION_PROMPT = (
    "Transcribe all visible text from this document image exactly as it appears.\n"
    "Rules:\n"
    "- Preserve the reading order, line breaks and field labels.\n"
    "- Preserve digits, identifiers and dates exactly; do not correct them.\n"
    "- Do not translate, summarise or explain anything.\n"
    "- Where text is illegible, write [ILLEGIBLE] in its place.\n"
    "Return the transcription only."
)


@dataclass
class OCRResult:
    text: str
    confidence: float
    engine: str
    notes: list[str] | None = None

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE_THRESHOLD


class OCREngine(Protocol):
    name: str

    def transcribe(self, image_bytes: bytes) -> OCRResult: ...


class TesseractEngine:
    """Classical OCR. Present only when the binary is installed."""

    name = "tesseract"

    @staticmethod
    def available() -> bool:
        if not (shutil.which("tesseract") or shutil.which("tesseract.exe")):
            return False
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, image_bytes: bytes) -> OCRResult:
        import io

        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        words: list[str] = []
        confidences: list[float] = []
        for text, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
            token = (text or "").strip()
            if not token:
                continue
            words.append(token)
            try:
                value = float(confidence)
            except (TypeError, ValueError):
                continue
            if value >= 0:  # Tesseract reports -1 for non-text blocks
                confidences.append(value / 100.0)

        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OCRResult(text=" ".join(words), confidence=confidence, engine=self.name)


class VisionModelEngine:
    """Transcription by the vision-language model.

    The model reports no calibrated confidence, so a conservative fixed value
    is used: high enough to proceed, low enough that nothing downstream treats
    a transcription as being as reliable as a real text layer.
    """

    name = "vision-model"
    ASSUMED_CONFIDENCE = 0.75

    def __init__(self, client=None) -> None:  # noqa: ANN001 - avoids an import cycle
        self._client = client

    def transcribe(self, image_bytes: bytes) -> OCRResult:
        from app.llm import get_llm_client

        client = self._client or get_llm_client()
        text = client.analyze_image(image_bytes, TRANSCRIPTION_PROMPT)
        cleaned = (text or "").strip()
        notes = []
        confidence = self.ASSUMED_CONFIDENCE
        if not cleaned:
            confidence = 0.0
            notes.append("the model returned no text for this page")
        elif "[ILLEGIBLE]" in cleaned:
            illegible = cleaned.count("[ILLEGIBLE]")
            confidence = max(0.3, self.ASSUMED_CONFIDENCE - 0.05 * illegible)
            notes.append(f"{illegible} illegible region(s) reported")
        return OCRResult(text=cleaned, confidence=confidence, engine=self.name, notes=notes)


def get_ocr_engine(client=None) -> OCREngine:  # noqa: ANN001
    """Prefer local OCR when it exists, fall back to the vision model."""
    if TesseractEngine.available():
        logger.debug("using tesseract for OCR")
        return TesseractEngine()
    return VisionModelEngine(client=client)

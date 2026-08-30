"""What every parser must produce.

PDFs, Word files, spreadsheets and photographs are wildly different objects,
but everything downstream — classification, extraction, evidence citation —
only wants ordered pages with text and, when needed, a rendered image. Each
parser reduces its format to that shape and nothing else.

The word "page" is literal for PDFs and images. For DOCX it is the whole
document (Word has no fixed pagination without rendering), and for spreadsheets
it is one worksheet. That mapping is recorded on the page so a report can cite
"Sheet 2" rather than an invented page number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Below this many characters a PDF page is treated as having no usable text
# layer, which routes it to OCR or the vision model.
MIN_TEXT_LAYER_CHARS = 50


class ParsingError(RuntimeError):
    """Raised when a file cannot be read at all. Carries an ErrorCode member."""

    def __init__(self, message: str, *, code: str = "PARSING_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ParsedTable:
    page_number: int
    rows: list[list[str]]
    name: str | None = None

    def to_text(self) -> str:
        return "\n".join(" | ".join(str(cell) for cell in row) for row in self.rows)


@dataclass
class ParsedPage:
    page_number: int
    text: str = ""
    width: float | None = None
    height: float | None = None
    has_text_layer: bool = False
    needs_ocr: bool = False
    ocr_used: bool = False
    ocr_confidence: float | None = None
    # Rendered bitmap, present only when a page has to be looked at rather
    # than read. Held in memory just long enough to be written to storage.
    image_bytes: bytes | None = None
    image_media_type: str = "image/png"
    tables: list[ParsedTable] = field(default_factory=list)
    label: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text or "")


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]
    page_count: int
    source_format: str
    is_readable: bool = True
    quality_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def total_chars(self) -> int:
        return sum(page.char_count for page in self.pages)

    def pages_needing_vision(self) -> list[ParsedPage]:
        return [page for page in self.pages if page.needs_ocr]

"""PDF parsing with PyMuPDF.

Handles both kinds of PDF the pipeline sees: born-digital files with a real
text layer, and scans that are really just images in a PDF wrapper. The
difference is detected per page rather than per document, because mixed files
are common — a typed application form with a photographed annexure stapled on
the end.
"""

from __future__ import annotations

from typing import BinaryIO

import fitz  # PyMuPDF

from app.extraction.base import (
    MIN_TEXT_LAYER_CHARS,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParsingError,
)

# 200 dpi renders small print legibly for a vision model without producing
# images so large they dominate the request.
RENDER_DPI = 200


def parse_pdf(
    source: str | BinaryIO | bytes,
    *,
    render_pages_needing_ocr: bool = True,
    max_render_pages: int = 40,
) -> ParsedDocument:
    try:
        if isinstance(source, bytes):
            document = fitz.open(stream=source, filetype="pdf")
        elif isinstance(source, str):
            document = fitz.open(source)
        else:
            document = fitz.open(stream=source.read(), filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises a wide range
        raise ParsingError(f"could not open the PDF: {exc}", code="CORRUPTED_FILE") from exc

    pages: list[ParsedPage] = []
    quality_flags: list[str] = []
    rendered = 0

    try:
        if document.needs_pass:
            raise ParsingError("the PDF is password protected", code="CORRUPTED_FILE")

        for index in range(document.page_count):
            page = document.load_page(index)
            text = page.get_text("text") or ""
            stripped = text.strip()
            has_text = len(stripped) >= MIN_TEXT_LAYER_CHARS
            rect = page.rect

            parsed = ParsedPage(
                page_number=index + 1,
                text=stripped,
                width=float(rect.width),
                height=float(rect.height),
                has_text_layer=has_text,
                needs_ocr=not has_text,
                label=f"Page {index + 1}",
            )

            if has_text:
                parsed.tables = _extract_tables(page, index + 1)
            elif render_pages_needing_ocr and rendered < max_render_pages:
                parsed.image_bytes = _render(page)
                rendered += 1
                parsed.notes.append("no text layer; rendered for vision or OCR")

            pages.append(parsed)

        if not pages:
            raise ParsingError("the PDF contains no pages", code="CORRUPTED_FILE")

        scanned_pages = sum(1 for page in pages if not page.has_text_layer)
        if scanned_pages == len(pages):
            quality_flags.append("SCANNED_DOCUMENT")
        if rendered >= max_render_pages and scanned_pages > max_render_pages:
            quality_flags.append("PARTIAL_RENDER")

        return ParsedDocument(
            pages=pages,
            page_count=len(pages),
            source_format="pdf",
            is_readable=any(page.has_text_layer or page.image_bytes for page in pages),
            quality_flags=quality_flags,
            metadata={
                "producer": document.metadata.get("producer") if document.metadata else None,
                "scanned_pages": scanned_pages,
                "rendered_pages": rendered,
            },
        )
    finally:
        document.close()


def _render(page: fitz.Page) -> bytes | None:
    try:
        pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
        return pixmap.tobytes("png")
    except Exception:  # noqa: BLE001 - a failed render must not lose the page
        return None


def _extract_tables(page: fitz.Page, page_number: int) -> list[ParsedTable]:
    """Table extraction is best-effort; a miss costs recall, not correctness."""
    try:
        finder = page.find_tables()
    except Exception:  # noqa: BLE001
        return []

    tables: list[ParsedTable] = []
    for table in getattr(finder, "tables", []):
        try:
            rows = [
                [("" if cell is None else str(cell).strip()) for cell in row]
                for row in table.extract()
            ]
        except Exception:  # noqa: BLE001
            continue
        if rows:
            tables.append(ParsedTable(page_number=page_number, rows=rows))
    return tables


def page_count(source: str | bytes) -> int:
    """Cheap page count for the upload path, before full parsing is queued."""
    try:
        document = fitz.open(stream=source, filetype="pdf") if isinstance(source, bytes) else fitz.open(source)
    except Exception as exc:  # noqa: BLE001
        raise ParsingError(f"could not open the PDF: {exc}", code="CORRUPTED_FILE") from exc
    try:
        return document.page_count
    finally:
        document.close()

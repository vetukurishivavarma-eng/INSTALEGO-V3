"""Format dispatch for document parsing."""

from __future__ import annotations

from pathlib import Path

from app.extraction.base import (
    MIN_TEXT_LAYER_CHARS,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParsingError,
)

PDF_EXTENSIONS = {".pdf"}
WORD_EXTENSIONS = {".doc", ".docx"}
EXCEL_EXTENSIONS = {".xls", ".xlsx"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | WORD_EXTENSIONS | EXCEL_EXTENSIONS | IMAGE_EXTENSIONS

MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# Leading bytes are checked against the claimed extension, because a browser
# will happily label anything .pdf and a parser should not be the thing that
# discovers otherwise.
_MAGIC = {
    ".pdf": [b"%PDF"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".docx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
    ".doc": [b"\xd0\xcf\x11\xe0"],
    ".xls": [b"\xd0\xcf\x11\xe0"],
}


def normalize_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_supported(filename: str) -> bool:
    return normalize_extension(filename) in SUPPORTED_EXTENSIONS


def guess_mime(filename: str) -> str:
    return MIME_BY_EXTENSION.get(normalize_extension(filename), "application/octet-stream")


def content_matches_extension(head: bytes, filename: str) -> bool:
    """True when the file's first bytes agree with its extension."""
    expected = _MAGIC.get(normalize_extension(filename))
    if not expected:
        return True
    return any(head.startswith(signature) for signature in expected)


def parse_document(path: str, *, filename: str | None = None) -> ParsedDocument:
    """Parse a stored file into pages, dispatching on its extension."""
    extension = normalize_extension(filename or path)

    if extension in PDF_EXTENSIONS:
        from app.extraction.pdf import parse_pdf

        return parse_pdf(path)
    if extension in WORD_EXTENSIONS:
        from app.extraction.docx import parse_docx

        return parse_docx(path)
    if extension in EXCEL_EXTENSIONS:
        from app.extraction.xlsx import parse_xlsx

        return parse_xlsx(path)
    if extension in IMAGE_EXTENSIONS:
        from app.extraction.image import parse_image

        return parse_image(path)

    raise ParsingError(f"unsupported file type: {extension or 'unknown'}", code="UNSUPPORTED_FILE")


__all__ = [
    "IMAGE_EXTENSIONS",
    "MIME_BY_EXTENSION",
    "MIN_TEXT_LAYER_CHARS",
    "SUPPORTED_EXTENSIONS",
    "ParsedDocument",
    "ParsedPage",
    "ParsedTable",
    "ParsingError",
    "content_matches_extension",
    "guess_mime",
    "is_supported",
    "normalize_extension",
    "parse_document",
]

"""Word document parsing.

Word has no fixed pagination until it is rendered, so the whole document is
presented as a single page. Evidence therefore cites the document and the
quoted text rather than a page number, which is honest: inventing "page 3" for
a .docx would put a number in a report that nothing can verify.

Legacy .doc is a different, binary format that python-docx cannot read. If
LibreOffice is on the path it is used to convert; otherwise the file is
rejected with a clear message instead of being silently half-parsed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.extraction.base import ParsedDocument, ParsedPage, ParsedTable, ParsingError


def parse_docx(path: str) -> ParsedDocument:
    source = Path(path)
    if source.suffix.lower() == ".doc":
        source = Path(_convert_legacy_doc(str(source)))

    try:
        document = DocxDocument(str(source))
    except Exception as exc:  # noqa: BLE001
        raise ParsingError(f"could not open the Word document: {exc}", code="CORRUPTED_FILE") from exc

    lines: list[str] = []
    headings: list[str] = []
    tables: list[ParsedTable] = []

    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = (block.style.name if block.style else "") or ""
            if style.lower().startswith("heading") or style.lower() == "title":
                headings.append(text)
                lines.append(f"## {text}")
            else:
                lines.append(text)
        else:
            rows = [
                [cell.text.strip() for cell in row.cells]
                for row in block.rows
            ]
            if rows:
                tables.append(ParsedTable(page_number=1, rows=rows))
                lines.append(ParsedTable(page_number=1, rows=rows).to_text())

    text = "\n".join(lines).strip()
    page = ParsedPage(
        page_number=1,
        text=text,
        has_text_layer=bool(text),
        needs_ocr=not text,
        tables=tables,
        label="Document body",
    )
    return ParsedDocument(
        pages=[page],
        page_count=1,
        source_format="docx",
        is_readable=bool(text),
        quality_flags=[] if text else ["UNREADABLE"],
        metadata={"headings": headings, "table_count": len(tables), "paginated": False},
    )


def _iter_blocks(document: DocxDocument):
    """Walk paragraphs and tables in document order.

    python-docx exposes the two collections separately, which loses the
    interleaving; reading the body XML preserves it so a table stays attached
    to the heading that introduces it.
    """
    from docx.oxml.ns import qn

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _convert_legacy_doc(path: str) -> str:
    """Convert .doc via LibreOffice when available."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ParsingError(
            "legacy .doc files need LibreOffice to convert; install it or supply a .docx",
            code="UNSUPPORTED_FILE",
        )
    out_dir = tempfile.mkdtemp(prefix="ldai-doc-")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", out_dir, path],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ParsingError(f"LibreOffice could not convert the .doc file: {exc}",
                           code="PARSING_FAILED") from exc
    converted = Path(out_dir) / (Path(path).stem + ".docx")
    if not converted.exists():
        raise ParsingError("LibreOffice produced no output for the .doc file",
                           code="PARSING_FAILED")
    return str(converted)

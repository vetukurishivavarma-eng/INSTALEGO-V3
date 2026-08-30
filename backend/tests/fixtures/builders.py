"""Builders for synthetic test documents.

Everything the test suite and the evaluation cases read is generated here, in
every format the system accepts. The data is invented: the names, numbers and
addresses belong to nobody, and the Aadhaar-style numbers are constructed to
carry a valid checksum only so the validation path can be exercised.

Generating rather than committing binaries keeps the fixtures inspectable and
lets an evaluation case vary one field at a time, which is exactly what a
discrepancy test needs.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_pdf(path: str | Path, title: str, fields: dict[str, str], *, pages: int = 1) -> Path:
    """A text-layer PDF laid out as label/value lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    items = list(fields.items())
    per_page = max(1, -(-len(items) // pages))
    for page_index in range(pages):
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(70, height - 80, title)
        pdf.setFont("Helvetica", 11)
        y = height - 120
        for label, value in items[page_index * per_page : (page_index + 1) * per_page]:
            pdf.drawString(70, y, f"{label}: {value}")
            y -= 22
        pdf.setFont("Helvetica-Oblique", 8)
        pdf.drawString(70, 50, f"Page {page_index + 1} of {pages}")
        pdf.showPage()
    pdf.save()
    return path


def make_scanned_pdf(path: str | Path, title: str, fields: dict[str, str]) -> Path:
    """A PDF with no text layer: an image of the text, as a scanner produces."""
    import fitz

    image_path = Path(path).with_suffix(".tmp.png")
    make_image(image_path, title, fields)

    path = Path(path)
    document = fitz.open()
    pixmap = fitz.Pixmap(str(image_path))
    page = document.new_page(width=pixmap.width, height=pixmap.height)
    page.insert_image(fitz.Rect(0, 0, pixmap.width, pixmap.height), filename=str(image_path))
    document.save(str(path))
    document.close()
    image_path.unlink(missing_ok=True)
    return path


def make_image(
    path: str | Path,
    title: str,
    fields: dict[str, str],
    *,
    size: tuple[int, int] = (1200, 900),
    blur: bool = False,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    scale = max(1.0, size[0] / 1200)
    heading = _font(int(34 * scale))
    body = _font(int(26 * scale))
    draw.text((60, 50), title, fill="black", font=heading)
    y = 120
    for label, value in fields.items():
        draw.text((60, y), f"{label}: {value}", fill="black", font=body)
        y += int(46 * scale)
    if blur:
        from PIL import ImageFilter

        image = image.filter(ImageFilter.GaussianBlur(radius=6))
    image.save(path)
    return path


def make_docx(path: str | Path, title: str, fields: dict[str, str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = DocxDocument()
    document.add_heading(title, level=1)
    document.add_paragraph("Submitted for credit assessment.")

    table = document.add_table(rows=0, cols=2)
    for label, value in fields.items():
        row = table.add_row().cells
        row[0].text = label
        row[1].text = str(value)
    document.save(str(path))
    return path


def make_xlsx(path: str | Path, sheets: dict[str, list[list[str]]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(title=name[:31])
        for row in rows:
            sheet.append(row)
    workbook.save(str(path))
    return path


def _font(size: int):
    """A scalable face, falling back to the bitmap default on older Pillow."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

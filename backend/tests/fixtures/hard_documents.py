"""Base documents rendered the way real ones are laid out.

``builders.py`` writes ``Label: value`` on consecutive lines, which is the
easiest possible reading task and the reason the existing evaluation scores
perfectly. The documents here keep the same invented applicant and the same
values, and change only the presentation:

- an identity card puts the label above the value in small type, in two
  columns, with the number spaced into groups;
- a payslip is a bordered table whose amounts are right-aligned a long way
  from the label that names them;
- a sanction letter states the amount in the middle of a sentence, with no
  label anywhere;
- a bank statement buries the account number in a letterhead block above a
  transaction table that contains other, wrong-looking numbers.

Each carries its ground truth keyed by the canonical field name the pipeline
stores, so a run can score extraction directly rather than inferring it from
whether a discrepancy fired.

Filenames are deliberately uninformative (``IMG_4471.jpg``). A real upload
often is, and a filename that says "Aadhaar" lets the classifier be right for
the wrong reason, which would flatter exactly the number this suite exists to
measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from tests.fixtures.degrade import DEGRADATIONS, font_of

INK = (17, 17, 17)
FAINT = (95, 95, 95)
RULE = (140, 140, 140)

# The one applicant every fixture describes. Same person as the evaluation
# cases, so numbers from the two suites can be read side by side.
APPLICANT = {
    "name": "Ravi Kumar",
    "father_name": "Suresh Kumar",
    "date_of_birth": "12/04/1998",
    "aadhaar": "2345 6789 0124",
    "pan": "ABCDE1234F",
    "address": "12, M.G. Road, Bengaluru - 560001",
    "employer": "Acme Technologies Pvt Ltd",
    "account": "000123456789",
}


@dataclass
class HardDocument:
    """One base document, its layout, and what a correct read produces."""

    doc_id: str
    description: str
    # Types a correct classification may land on. More than one is allowed
    # where the taxonomy genuinely admits two readings: a sanction letter is
    # an AGREEMENT, but calling it a LEGAL_DOCUMENT is not a mistake.
    expected_type: tuple[str, ...]
    render: Callable[[], Image.Image]
    # canonical field name -> the value a correct extraction reports
    truth: dict[str, str] = field(default_factory=dict)
    # Values that appear on the page but are NOT the answer: a decoy the
    # extractor can plausibly grab instead. Reported separately, because
    # picking one up is a different failure from reading a digit wrong.
    decoys: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Layout toolkit
# --------------------------------------------------------------------------
def _page(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), (252, 252, 250))
    return image, ImageDraw.Draw(image)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    words = text.split()
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _paragraph(draw, x: int, y: int, text: str, font, width: int, leading: int) -> int:
    for line in _wrap(draw, text, font, width):
        draw.text((x, y), line, font=font, fill=INK)
        y += leading
    return y


# --------------------------------------------------------------------------
# The documents
# --------------------------------------------------------------------------
def _render_aadhaar() -> Image.Image:
    """A card: label above value, two columns, the number spaced into groups
    and set apart from everything that names it."""
    image, draw = _page(1500, 950)
    title = font_of(38, bold=True)
    small = font_of(21)
    label = font_of(20)
    value = font_of(31, bold=True)
    digits = font_of(46, bold=True)

    draw.rectangle([0, 0, 1500, 118], fill=(233, 236, 244))
    draw.text((48, 30), "GOVERNMENT OF INDIA", font=title, fill=(120, 30, 30))
    draw.text((48, 78), "UNIQUE IDENTIFICATION AUTHORITY OF INDIA", font=small, fill=FAINT)

    # Photograph placeholder, where the real card carries one.
    draw.rectangle([48, 160, 328, 530], outline=RULE, width=3)
    draw.line([48, 160, 328, 530], fill=(225, 225, 225), width=2)
    draw.line([328, 160, 48, 530], fill=(225, 225, 225), width=2)

    left = 380
    right = 980
    rows_left = [("Name", APPLICANT["name"]), ("Date of Birth", APPLICANT["date_of_birth"])]
    rows_right = [("Gender", "Male"), ("Enrolment No.", "1094/28841/03772")]

    for column, rows in ((left, rows_left), (right, rows_right)):
        y = 170
        for name, text in rows:
            draw.text((column, y), name.upper(), font=label, fill=FAINT)
            draw.text((column, y + 30), text, font=value, fill=INK)
            y += 130

    draw.line([48, 590, 1452, 590], fill=RULE, width=2)
    draw.text((48, 630), APPLICANT["aadhaar"], font=digits, fill=INK)
    draw.text((640, 648), "Aadhaar - Aam Aadmi ka Adhikar", font=small, fill=FAINT)

    draw.text((48, 740), "ADDRESS", font=label, fill=FAINT)
    _paragraph(draw, 48, 774, APPLICANT["address"], font_of(26), 900, 36)
    return image


def _render_pan() -> Image.Image:
    """The PAN card layout, where the number sits under a caption in a
    different column from the name and the date it belongs to."""
    image, draw = _page(1500, 950)
    draw.rectangle([0, 0, 1500, 106], fill=(240, 236, 226))
    draw.text((48, 24), "INCOME TAX DEPARTMENT", font=font_of(34, bold=True), fill=(30, 60, 120))
    draw.text((48, 68), "GOVT. OF INDIA", font=font_of(22), fill=FAINT)

    label = font_of(21)
    value = font_of(33, bold=True)

    entries = [
        ("Permanent Account Number", APPLICANT["pan"]),
        ("Name", APPLICANT["name"].upper()),
        ("Father's Name", APPLICANT["father_name"].upper()),
        ("Date of Birth", APPLICANT["date_of_birth"]),
    ]
    y = 170
    for name, text in entries:
        draw.text((60, y), name, font=label, fill=FAINT)
        draw.text((60, y + 32), text, font=value, fill=INK)
        y += 150

    draw.rectangle([1120, 170, 1430, 560], outline=RULE, width=3)
    draw.text((1130, 600), "Signature", font=label, fill=FAINT)
    draw.line([1120, 640, 1430, 640], fill=INK, width=2)
    return image


def _render_payslip() -> Image.Image:
    """A bordered table. Every amount is right-aligned in a column, far from
    the word that names it, and the total the pipeline wants is one row among
    several plausible ones."""
    image, draw = _page(1500, 1900)
    draw.text((60, 50), APPLICANT["employer"].upper(), font=font_of(36, bold=True), fill=INK)
    draw.text((60, 100), "Plot 44, Whitefield, Bengaluru 560066", font=font_of(22), fill=FAINT)
    draw.text((60, 160), "PAYSLIP FOR THE MONTH OF JULY 2026",
              font=font_of(28, bold=True), fill=INK)
    draw.line([60, 205, 1440, 205], fill=INK, width=3)

    header = font_of(23)
    body = font_of(26)
    strong = font_of(28, bold=True)

    meta = [
        ("Employee", APPLICANT["name"]),
        ("Employee Code", "ACM-20481"),
        ("Designation", "Senior Engineer"),
        ("PAN", APPLICANT["pan"]),
        ("Bank A/c", APPLICANT["account"]),
        ("Days Paid", "31.00"),
    ]
    y = 240
    for index, (name, text) in enumerate(meta):
        column = 60 if index % 2 == 0 else 780
        if index % 2 == 0 and index:
            y += 52
        draw.text((column, y), name, font=header, fill=FAINT)
        draw.text((column + 250, y), text, font=body, fill=INK)
    y += 90

    draw.rectangle([60, y, 1440, y + 62], fill=(238, 238, 238))
    draw.text((80, y + 18), "EARNINGS", font=strong, fill=INK)
    draw.text((560, y + 18), "AMOUNT (INR)", font=strong, fill=INK)
    draw.text((820, y + 18), "DEDUCTIONS", font=strong, fill=INK)
    draw.text((1240, y + 18), "AMOUNT (INR)", font=strong, fill=INK)
    y += 62

    earnings = [("Basic", "25,000.00"), ("House Rent Allowance", "12,500.00"),
                ("Conveyance", "4,000.00"), ("Special Allowance", "8,500.00")]
    deductions = [("Provident Fund", "3,000.00"), ("Professional Tax", "200.00"),
                  ("Income Tax (TDS)", "4,800.00"), ("", "")]

    for (earning, amount), (deduction, taken) in zip(earnings, deductions, strict=True):
        draw.rectangle([60, y, 1440, y + 56], outline=RULE, width=1)
        draw.line([760, y, 760, y + 56], fill=RULE, width=1)
        draw.text((80, y + 14), earning, font=body, fill=INK)
        draw.text((740 - draw.textlength(amount, font=body), y + 14), amount, font=body, fill=INK)
        if deduction:
            draw.text((820, y + 14), deduction, font=body, fill=INK)
            draw.text((1420 - draw.textlength(taken, font=body), y + 14), taken,
                      font=body, fill=INK)
        y += 56

    draw.rectangle([60, y, 1440, y + 66], fill=(244, 244, 240))
    draw.text((80, y + 18), "Gross Earnings", font=strong, fill=INK)
    draw.text((740 - draw.textlength("50,000.00", font=strong), y + 18), "50,000.00",
              font=strong, fill=INK)
    draw.text((820, y + 18), "Total Deductions", font=strong, fill=INK)
    draw.text((1420 - draw.textlength("8,000.00", font=strong), y + 18), "8,000.00",
              font=strong, fill=INK)
    y += 110

    draw.rectangle([60, y, 1440, y + 78], outline=INK, width=3)
    draw.text((80, y + 22), "NET PAY FOR THE MONTH", font=strong, fill=INK)
    net = "42,000.00"
    draw.text((1420 - draw.textlength(net, font=font_of(34, bold=True)), y + 18), net,
              font=font_of(34, bold=True), fill=INK)
    y += 130

    _paragraph(draw, 60, y,
               "Rupees Forty Two Thousand Only. This is a computer generated payslip and "
               "does not require a signature.", font_of(22), 1380, 34)
    return image


def _render_sanction_letter() -> Image.Image:
    """Prose. The amount and the applicant are stated inside sentences, and
    there is no label on the page to anchor to."""
    image, draw = _page(1500, 1980)
    draw.text((60, 60), "STATE BANK", font=font_of(40, bold=True, serif=True), fill=(20, 50, 110))
    draw.text((60, 112), "Retail Credit Department, Bengaluru Main Branch",
              font=font_of(23, serif=True), fill=FAINT)
    draw.line([60, 158, 1440, 158], fill=(20, 50, 110), width=3)

    body = font_of(27, serif=True)
    draw.text((1120, 200), "Ref: RCD/BLR/2026/4471", font=font_of(23, serif=True), fill=INK)
    draw.text((1120, 240), "01 August 2026", font=font_of(23, serif=True), fill=INK)

    y = 320
    y = _paragraph(draw, 60, y, "To,", body, 1380, 42)
    y = _paragraph(draw, 60, y, f"{APPLICANT['name']},", body, 1380, 42)
    y = _paragraph(draw, 60, y, APPLICANT["address"], body, 1380, 42) + 40

    draw.text((60, y), "Sub: Sanction of personal loan facility", font=font_of(28, bold=True,
                                                                              serif=True), fill=INK)
    y += 80

    paragraphs = [
        "Dear Sir,",
        "With reference to your application dated 24 July 2026 and the documents "
        "submitted in support thereof, we are pleased to inform you that the Bank has "
        "sanctioned a personal loan facility of Rupees Five Lakh only "
        "(Rs. 5,00,000/-) in your favour, repayable over sixty equated monthly "
        "instalments of Rs. 10,610/- each.",
        "The facility carries interest at 10.75 per cent per annum on a reducing "
        "balance, and is subject to a one time processing fee of Rs. 5,900/- "
        "recoverable from the first disbursement. The sanction is valid for thirty "
        "days from the date of this letter.",
        "This sanction is issued on the basis of the particulars furnished by you, "
        "including your date of birth recorded as 12 April 1998 and permanent account "
        "number ABCDE1234F, and is liable to be withdrawn should any particular be "
        "found to be incorrect.",
        "Yours faithfully,",
    ]
    for text in paragraphs:
        y = _paragraph(draw, 60, y, text, body, 1380, 44) + 30

    draw.text((60, y + 40), "Authorised Signatory", font=body, fill=INK)
    return image


def _render_bank_statement() -> Image.Image:
    """A letterhead block above a transaction table. The account number sits
    among a customer ID, an IFSC code and a branch code, and the closing
    balance is the last of many numbers in the same column."""
    image, draw = _page(1500, 1980)
    draw.rectangle([0, 0, 1500, 130], fill=(20, 50, 110))
    draw.text((60, 34), "STATE BANK", font=font_of(38, bold=True), fill=(255, 255, 255))
    draw.text((60, 84), "Statement of Account", font=font_of(23), fill=(215, 220, 235))

    label = font_of(22)
    body = font_of(25)

    block = [
        ("Account Holder", APPLICANT["name"]),
        ("Account Number", APPLICANT["account"]),
        ("Customer ID", "884120973"),
        ("IFSC", "SBIN0004521"),
        ("Branch Code", "004521"),
        ("Statement Period", "01/07/2026 to 31/07/2026"),
    ]
    y = 180
    for name, text in block:
        draw.text((60, y), name, font=label, fill=FAINT)
        draw.text((420, y), text, font=body, fill=INK)
        y += 46

    draw.text((60, y + 20), "Address on record", font=label, fill=FAINT)
    _paragraph(draw, 420, y + 20, APPLICANT["address"], body, 900, 34)
    y += 130

    draw.rectangle([60, y, 1440, y + 54], fill=(238, 240, 246))
    for text, column in (("Date", 80), ("Particulars", 280), ("Debit", 820),
                         ("Credit", 1030), ("Balance", 1260)):
        draw.text((column, y + 14), text, font=font_of(24, bold=True), fill=INK)
    y += 54

    rows = [
        ("01/07/2026", "Opening balance", "", "", "88,410.00"),
        ("05/07/2026", "ACME TECHNOLOGIES SALARY", "", "42,000.00", "1,30,410.00"),
        ("08/07/2026", "UPI/BESCOM/ELECTRICITY", "2,310.00", "", "1,28,100.00"),
        ("14/07/2026", "ATM WDL BLR MG ROAD", "5,000.00", "", "1,23,100.00"),
        ("22/07/2026", "NEFT/RENT/LANDLORD", "18,000.00", "", "1,05,100.00"),
        ("28/07/2026", "INTEREST CREDIT", "", "19,900.00", "1,25,000.00"),
    ]
    for date, particulars, debit, credit, balance in rows:
        draw.rectangle([60, y, 1440, y + 50], outline=RULE, width=1)
        draw.text((80, y + 12), date, font=body, fill=INK)
        draw.text((280, y + 12), particulars, font=font_of(23), fill=INK)
        for amount, edge in ((debit, 1000), (credit, 1210), (balance, 1430)):
            if amount:
                draw.text((edge - draw.textlength(amount, font=body), y + 12), amount,
                          font=body, fill=INK)
        y += 50

    y += 30
    draw.text((820, y), "Closing Balance", font=font_of(26, bold=True), fill=INK)
    closing = "1,25,000.00"
    draw.text((1430 - draw.textlength(closing, font=font_of(28, bold=True)), y - 2), closing,
              font=font_of(28, bold=True), fill=INK)
    return image


DOCUMENTS: dict[str, HardDocument] = {
    document.doc_id: document
    for document in [
        HardDocument(
            doc_id="aadhaar",
            description="identity card: label above value, two columns, spaced digit groups",
            expected_type=("AADHAAR", "IDENTITY_PROOF"),
            render=_render_aadhaar,
            truth={
                "name": APPLICANT["name"],
                "date_of_birth": APPLICANT["date_of_birth"],
                "aadhaar": APPLICANT["aadhaar"],
                "current_address": APPLICANT["address"],
            },
            decoys={"aadhaar": "1094/28841/03772"},
        ),
        HardDocument(
            doc_id="pan",
            description="PAN card: the number captioned, in its own block",
            expected_type=("PAN", "IDENTITY_PROOF"),
            render=_render_pan,
            truth={
                "name": APPLICANT["name"],
                "pan": APPLICANT["pan"],
                "date_of_birth": APPLICANT["date_of_birth"],
                "father_name": APPLICANT["father_name"],
            },
            decoys={"name": APPLICANT["father_name"]},
        ),
        HardDocument(
            doc_id="payslip",
            description="bordered table: amounts right-aligned away from their labels",
            expected_type=("SALARY_SLIP", "EMPLOYMENT_PROOF"),
            render=_render_payslip,
            truth={
                "name": APPLICANT["name"],
                "employer": APPLICANT["employer"],
                "designation": "Senior Engineer",
                "income": "50000",
                "net_salary": "42000",
            },
            decoys={"income": "25000 (basic pay row)", "net_salary": "8000 (total deductions)"},
        ),
        HardDocument(
            doc_id="sanction",
            description="prose letter: the amount stated mid-sentence, no labels",
            expected_type=("AGREEMENT", "LEGAL_DOCUMENT"),
            render=_render_sanction_letter,
            truth={
                "party_two": APPLICANT["name"],
                "loan_amount": "500000",
                "agreement_date": "01/08/2026",
            },
            decoys={"loan_amount": "10610 (instalment) or 5900 (processing fee)"},
        ),
        HardDocument(
            doc_id="statement",
            description="letterhead plus transaction table: the account number among four codes",
            expected_type=("BANK_STATEMENT", "FINANCIAL_STATEMENT"),
            render=_render_bank_statement,
            truth={
                "name": APPLICANT["name"],
                "bank_account": APPLICANT["account"],
                "bank_name": "State Bank",
                "closing_balance": "125000",
                "current_address": APPLICANT["address"],
            },
            decoys={"bank_account": "884120973 (customer ID) or 004521 (branch code)"},
        ),
    ]
}

# One representative of each layout problem, for a run on a rate-limited
# endpoint where the whole matrix will not fit in a day's quota.
CORE_DOCUMENTS = ("aadhaar", "payslip", "sanction")

_CACHE: dict[str, Image.Image] = {}


def _trim(image: Image.Image, margin: int = 70) -> Image.Image:
    """Crop trailing blank paper, keeping a margin.

    The extractor downscales anything over 1800px on its longest side, so an
    inch of empty page at the bottom is resolution taken away from the text.
    """
    from PIL import ImageChops

    background = Image.new("RGB", image.size, image.getpixel((2, image.size[1] - 2)))
    box = ImageChops.difference(image.convert("RGB"), background).convert("L").getbbox()
    if box is None:
        return image
    bottom = min(image.size[1], box[3] + margin)
    return image.crop((0, 0, image.size[0], bottom))


def base_image(doc_id: str) -> Image.Image:
    """The clean render, cached: degradations all start from the same pixels."""
    if doc_id not in _CACHE:
        _CACHE[doc_id] = _trim(DOCUMENTS[doc_id].render())
    return _CACHE[doc_id].copy()


def build_variant(
    doc_id: str,
    degradation: str,
    directory: str | Path,
    *,
    fmt: str = "jpg",
    filename: str | None = None,
) -> Path:
    """Render one document under one degradation and write it to disk.

    JPEG by default, because that is what a phone produces and what a bank
    actually receives; ``fmt="pdf"`` wraps the same pixels in an image-only PDF
    with no text layer, which is what a scanner produces.
    """
    image = DEGRADATIONS[degradation].apply(base_image(doc_id))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # Uninformative by design: see the module docstring. Derived from a stable
    # checksum rather than hash(), which is salted per interpreter and would
    # give the same fixture a different name on every run.
    from zlib import crc32

    stem = filename or f"IMG_{crc32(f'{doc_id}/{degradation}'.encode()) % 9000 + 1000}"

    if fmt == "pdf":
        import fitz

        temporary = directory / f"{stem}.tmp.png"
        image.save(temporary)
        path = directory / f"{stem}.pdf"
        pdf = fitz.open()
        pixmap = fitz.Pixmap(str(temporary))
        page = pdf.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(fitz.Rect(0, 0, pixmap.width, pixmap.height), filename=str(temporary))
        pdf.save(str(path))
        pdf.close()
        temporary.unlink(missing_ok=True)
        return path

    extension = "jpg" if fmt in {"jpg", "jpeg"} else fmt
    path = directory / f"{stem}.{extension}"
    if extension == "jpg":
        image.convert("RGB").save(path, format="JPEG", quality=88)
    else:
        image.save(path)
    return path

"""Parsing across every supported format."""

import pytest

from app.extraction import (
    ParsingError,
    content_matches_extension,
    guess_mime,
    is_supported,
    parse_document,
)
from tests.fixtures.builders import (
    make_docx,
    make_image,
    make_pdf,
    make_scanned_pdf,
    make_xlsx,
)

APPLICANT = {
    "Name": "Ravi Kumar",
    "Date of Birth": "12/04/1998",
    "PAN": "ABCDE1234F",
    "Address": "12 MG Road, Bengaluru 560001",
}


class TestDispatch:
    def test_supported_extensions(self):
        assert is_supported("a.pdf") and is_supported("a.DOCX") and is_supported("a.jpeg")
        assert not is_supported("a.exe")

    def test_mime_lookup(self):
        assert guess_mime("a.pdf") == "application/pdf"
        assert guess_mime("a.unknown") == "application/octet-stream"

    def test_magic_bytes_must_agree_with_the_extension(self):
        assert content_matches_extension(b"%PDF-1.7", "x.pdf")
        assert not content_matches_extension(b"MZ\x90\x00", "x.pdf")

    def test_unsupported_type_is_rejected_clearly(self, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("hello")
        with pytest.raises(ParsingError) as exc:
            parse_document(str(target))
        assert exc.value.code == "UNSUPPORTED_FILE"


class TestPdf:
    def test_text_layer_is_extracted(self, tmp_path):
        path = make_pdf(tmp_path / "pan.pdf", "PERMANENT ACCOUNT NUMBER", APPLICANT)
        parsed = parse_document(str(path))
        assert parsed.page_count == 1
        assert parsed.pages[0].has_text_layer is True
        assert "ABCDE1234F" in parsed.pages[0].text
        assert parsed.pages[0].needs_ocr is False

    def test_page_dimensions_are_recorded(self, tmp_path):
        path = make_pdf(tmp_path / "a.pdf", "Title", APPLICANT)
        page = parse_document(str(path)).pages[0]
        assert page.width and page.height and page.height > page.width

    def test_multipage(self, tmp_path):
        fields = {f"Field {i}": f"Value {i}" for i in range(12)}
        path = make_pdf(tmp_path / "multi.pdf", "Loan File", fields, pages=3)
        parsed = parse_document(str(path))
        assert parsed.page_count == 3
        assert [p.page_number for p in parsed.pages] == [1, 2, 3]

    def test_scanned_pdf_is_detected_and_rendered(self, tmp_path):
        path = make_scanned_pdf(tmp_path / "scan.pdf", "AADHAAR", APPLICANT)
        parsed = parse_document(str(path))
        page = parsed.pages[0]
        assert page.has_text_layer is False
        assert page.needs_ocr is True
        assert page.image_bytes, "a page with no text layer must be rendered for vision"
        assert "SCANNED_DOCUMENT" in parsed.quality_flags

    def test_corrupt_pdf_reports_a_useful_code(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4 this is not really a pdf")
        with pytest.raises(ParsingError) as exc:
            parse_document(str(path))
        assert exc.value.code == "CORRUPTED_FILE"


class TestDocx:
    def test_paragraphs_and_tables(self, tmp_path):
        path = make_docx(tmp_path / "application.docx", "LOAN APPLICATION", APPLICANT)
        parsed = parse_document(str(path))
        assert parsed.page_count == 1
        text = parsed.pages[0].text
        assert "LOAN APPLICATION" in text
        assert "Ravi Kumar" in text
        assert parsed.pages[0].tables, "the field table should be captured"

    def test_headings_are_recorded(self, tmp_path):
        path = make_docx(tmp_path / "a.docx", "LOAN APPLICATION", APPLICANT)
        parsed = parse_document(str(path))
        assert "LOAN APPLICATION" in parsed.metadata["headings"]

    def test_pagination_is_not_invented(self, tmp_path):
        path = make_docx(tmp_path / "a.docx", "T", APPLICANT)
        parsed = parse_document(str(path))
        assert parsed.metadata["paginated"] is False


class TestXlsx:
    def test_each_sheet_becomes_a_page(self, tmp_path):
        path = make_xlsx(
            tmp_path / "statement.xlsx",
            {
                "Summary": [["Account Holder", "Ravi Kumar"], ["Closing Balance", "125000"]],
                "Transactions": [["Date", "Amount"], ["01/04/2026", "5000"]],
            },
        )
        parsed = parse_document(str(path))
        assert parsed.page_count == 2
        assert parsed.pages[0].label == "Sheet: Summary"
        assert "Ravi Kumar" in parsed.pages[0].text
        assert parsed.pages[1].tables[0].rows[1] == ["01/04/2026", "5000"]

    def test_empty_rows_are_skipped(self, tmp_path):
        path = make_xlsx(tmp_path / "sparse.xlsx", {"S": [["A"], [], [], ["B"]]})
        parsed = parse_document(str(path))
        assert parsed.pages[0].tables[0].rows == [["A"], ["B"]]

    def test_legacy_xls_is_rejected_with_guidance(self, tmp_path):
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0legacy")
        with pytest.raises(ParsingError) as exc:
            parse_document(str(path))
        assert exc.value.code == "UNSUPPORTED_FILE"
        assert ".xlsx" in str(exc.value)


class TestImages:
    def test_image_needs_vision_and_carries_pixels(self, tmp_path):
        path = make_image(tmp_path / "aadhaar.png", "AADHAAR", APPLICANT)
        parsed = parse_document(str(path))
        page = parsed.pages[0]
        assert page.needs_ocr is True
        assert page.has_text_layer is False
        assert page.image_bytes

    def test_blurred_image_is_flagged_before_anything_reads_it(self, tmp_path):
        path = make_image(tmp_path / "blurred.png", "AADHAAR", APPLICANT, blur=True)
        parsed = parse_document(str(path))
        assert "UNCLEAR_IMAGE" in parsed.quality_flags
        assert parsed.is_readable is False

    def test_sharp_image_is_not_flagged(self, tmp_path):
        path = make_image(tmp_path / "sharp.png", "AADHAAR", APPLICANT)
        parsed = parse_document(str(path))
        assert "UNCLEAR_IMAGE" not in parsed.quality_flags

    def test_low_resolution_is_flagged(self, tmp_path):
        path = make_image(tmp_path / "tiny.png", "AADHAAR", APPLICANT, size=(320, 240))
        parsed = parse_document(str(path))
        assert "UNCLEAR_IMAGE" in parsed.quality_flags

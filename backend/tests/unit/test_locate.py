"""Recovering where on a page a value was printed.

A highlight over the exact words is the difference between a reviewer trusting
a finding in a second and hunting for it in a forty-page deed. The model is
asked for a bounding box and mostly does not supply one, so for text-layer
documents the position is recovered by searching the page.

The refusals matter as much as the hits. A rectangle in the wrong place is a
confident, checkable claim that is false, and worse than no highlight at all —
so anything the search cannot pin down returns nothing.
"""

from __future__ import annotations

import pytest

from app.extraction.locate import clear_cache, locate_text
from tests.fixtures.builders import make_pdf

FIELDS = {
    "Name": "Ravi Kumar",
    "PAN": "ABCDE1234F",
    "Date of Birth": "12/04/1998",
    "Address": "12, M.G. Road, Bengaluru - 560001",
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def page(tmp_path):
    return str(make_pdf(tmp_path / "PAN.pdf", "PERMANENT ACCOUNT NUMBER", FIELDS))


class TestFindingAValue:
    def test_a_value_on_the_page_is_located(self, page):
        box = locate_text(page, 1, "ABCDE1234F")
        assert len(box) == 4, box

    def test_the_box_is_a_percentage_of_the_page(self, page):
        """The viewer draws with percentages, so they survive the page being
        rendered at any scale."""
        x0, y0, x1, y1 = locate_text(page, 1, "ABCDE1234F")
        assert 0 <= x0 < x1 <= 100
        assert 0 <= y0 < y1 <= 100

    def test_different_values_land_in_different_places(self, page):
        pan = locate_text(page, 1, "ABCDE1234F")
        name = locate_text(page, 1, "Ravi Kumar")
        assert pan != name
        # The fields are written top to bottom in the order given.
        assert name[1] < pan[1]

    def test_the_snippet_is_tried_before_the_bare_value(self, page):
        """Callers pass the quoted span first and the value as a fallback,
        because a model paraphrases a snippet far more often than it alters
        the value it read."""
        located = locate_text(page, 1, "PAN: ABCDE1234F", "ABCDE1234F")
        assert len(located) == 4

    def test_the_fallback_is_used_when_the_snippet_is_a_paraphrase(self, page):
        located = locate_text(page, 1, "the PAN number shown on the card", "ABCDE1234F")
        assert len(located) == 4

    def test_a_wrapped_value_is_covered_by_one_box(self, tmp_path):
        long_address = (
            "Flat 402, Sunrise Residency, 14th Cross, 3rd Main, "
            "Jayanagar 7th Block, Bengaluru, Karnataka - 560070"
        )
        path = str(make_pdf(tmp_path / "Bill.pdf", "ELECTRICITY BILL",
                            {"Address": long_address}))
        box = locate_text(path, 1, long_address)
        # Either one rectangle or a refusal; never a partial one.
        assert box == [] or len(box) == 4


class TestRefusing:
    def test_a_value_that_is_not_there_returns_nothing(self, page):
        assert locate_text(page, 1, "Sunita Sharma") == []

    def test_a_page_that_does_not_exist_returns_nothing(self, page):
        assert locate_text(page, 9, "ABCDE1234F") == []
        assert locate_text(page, 0, "ABCDE1234F") == []

    def test_a_search_term_too_short_to_mean_anything_is_refused(self, page):
        """A one or two character term matches all over the page, and the first
        hit would be arbitrary."""
        assert locate_text(page, 1, "R") == []
        assert locate_text(page, 1, "12") == []

    def test_an_empty_or_missing_candidate_is_skipped(self, page):
        assert locate_text(page, 1, None, "") == []
        assert locate_text(page, 1, None, "ABCDE1234F") != []

    def test_a_missing_file_does_not_raise(self, tmp_path):
        """A lost highlight must never fail a document."""
        assert locate_text(str(tmp_path / "gone.pdf"), 1, "ABCDE1234F") == []

    def test_an_unreadable_file_does_not_raise(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"not a pdf at all")
        assert locate_text(str(path), 1, "ABCDE1234F") == []


class TestTheCallerDecidesWhatIsSearchable:
    """PyMuPDF will open more than PDFs, so restricting the search to files
    with a real text layer is the workflow's job, not the locator's. A scan has
    nothing to search and a rectangle guessed over one would be worse than no
    highlight at all."""

    def _document(self, filename):
        class _Doc:
            pass

        doc = _Doc()
        doc.filename = filename
        return doc

    def test_a_scan_is_not_searched(self, monkeypatch):
        from app.workflows import extraction_workflow

        called = []
        monkeypatch.setattr(extraction_workflow, "locate_text",
                            lambda *a, **k: called.append(a) or [1.0, 2.0, 3.0, 4.0])
        assert extraction_workflow._locate(self._document("scan.jpg"), 1, "x", "y") == []
        assert called == []

    def test_a_pdf_is_searched(self, monkeypatch):
        from app.workflows import extraction_workflow

        monkeypatch.setattr(extraction_workflow, "locate_text",
                            lambda *a, **k: [1.0, 2.0, 3.0, 4.0])
        monkeypatch.setattr(extraction_workflow.document_service, "local_path",
                            lambda document: "somewhere.pdf")
        assert extraction_workflow._locate(self._document("deed.pdf"), 1, "x", "y") == [
            1.0, 2.0, 3.0, 4.0
        ]

    def test_a_page_number_that_makes_no_sense_is_not_searched(self, monkeypatch):
        from app.workflows import extraction_workflow

        called = []
        monkeypatch.setattr(extraction_workflow, "locate_text",
                            lambda *a, **k: called.append(a) or [1.0, 2.0, 3.0, 4.0])
        assert extraction_workflow._locate(self._document("deed.pdf"), 0, "x", "y") == []
        assert called == []

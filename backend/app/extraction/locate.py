"""Finding where on a page a value was printed.

A highlight over the exact words is the difference between a reviewer trusting
a finding in a second and hunting for it in a forty-page deed. The model is
asked for a bounding box and mostly does not supply one — it is reading text,
not measuring a page — so for anything with a text layer the position is
recovered here instead, by looking for the value in the page itself.

This is deliberately not clever. It searches for a string and reports where the
string is. If the value does not appear verbatim, nothing is returned and the
evidence falls back to the quoted text, which is what happened before. An
approximate rectangle would be worse than none: a highlight drawn over the
wrong line is a confident, checkable claim that is false.

Coordinates come back as percentages of the page, which is what the viewer
draws with and what survives the page being rendered at any scale.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# A span covering most of the page is not a location, it is a page. Almost
# always a one-character search term matching everywhere.
MAX_COVERAGE = 0.60
MIN_SEARCH_LENGTH = 3


def locate_text(path: str, page_number: int, *candidates: str | None) -> list[float]:
    """Percentage bbox of the first candidate found on the page, or ``[]``.

    Candidates are tried in order, so a caller can pass the quoted snippet
    first and the bare value as a fallback — a model paraphrases a snippet far
    more often than it alters the value it read.
    """
    for needle in candidates:
        cleaned = " ".join(str(needle or "").split())
        if len(cleaned) < MIN_SEARCH_LENGTH:
            continue
        found = _search(path, page_number, cleaned)
        if found:
            return found
    return []


@lru_cache(maxsize=256)
def _search(path: str, page_number: int, needle: str) -> tuple[float, ...] | None:
    """One search, cached: a page is asked about once per extracted field."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - PyMuPDF is a hard dependency
        return None

    try:
        with fitz.open(path) as document:
            if not 1 <= page_number <= document.page_count:
                return None
            page = document[page_number - 1]
            rects = page.search_for(needle)
            if not rects:
                return None

            box = rects[0]
            for other in rects[1:]:
                # A value wrapped across two lines comes back as two rectangles.
                if abs(other.y0 - box.y0) < box.height:
                    box |= other

            width, height = page.rect.width, page.rect.height
            if not width or not height:
                return None
            if (box.width * box.height) / (width * height) > MAX_COVERAGE:
                return None

            return (
                round(box.x0 / width * 100, 3),
                round(box.y0 / height * 100, 3),
                round(box.x1 / width * 100, 3),
                round(box.y1 / height * 100, 3),
            )
    except Exception as exc:  # noqa: BLE001 - a missing highlight is not a failed document
        logger.debug("could not locate %r on page %d: %s", needle, page_number, exc)
        return None


def clear_cache() -> None:
    _search.cache_clear()

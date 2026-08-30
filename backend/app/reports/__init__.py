"""Report generation: canonical analysis in, deterministic documents out."""

from app.reports.docx import render_docx
from app.reports.generator import (
    TemplateNotFoundError,
    build_report,
    load_template,
    reset_template_cache,
)
from app.reports.pdf import render_pdf

__all__ = [
    "TemplateNotFoundError",
    "build_report",
    "load_template",
    "render_docx",
    "render_pdf",
    "reset_template_cache",
]

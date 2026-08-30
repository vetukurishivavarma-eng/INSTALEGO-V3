"""PDF rendering with ReportLab.

Same contract as the DOCX renderer: it prints the report JSON against the
template's section list and invents nothing. The two renderers are kept
separate rather than converting DOCX to PDF, because that conversion needs
LibreOffice and would make PDF output depend on a binary that may not exist.
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SEVERITY_COLOURS = {
    "HIGH": colors.HexColor("#B3261E"),
    "MEDIUM": colors.HexColor("#B56A00"),
    "LOW": colors.HexColor("#4A4A4A"),
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=18, spaceAfter=6),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=9, textColor=colors.grey, spaceAfter=12
        ),
        "heading": ParagraphStyle(
            "heading", parent=base["Heading2"], fontSize=13, spaceBefore=14, spaceAfter=6
        ),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12,
                               alignment=TA_LEFT),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=7.5,
                                textColor=colors.grey, leading=10),
    }


def render_pdf(report: dict[str, Any], template: dict[str, Any]) -> bytes:
    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=report.get("title", "Document Verification Report"),
    )

    story: list[Any] = [Paragraph(report.get("title", "Report"), styles["title"])]
    if report.get("subtitle"):
        story.append(Paragraph(report["subtitle"], styles["subtitle"]))

    for section in template.get("sections", []):
        key = section.get("key")
        content = report.get(key)
        story.append(Paragraph(section.get("title", key), styles["heading"]))

        if content in (None, [], {}):
            story.append(Paragraph("Nothing to report in this section.", styles["body"]))
            continue

        section_type = section.get("type", "object")
        if section_type == "findings":
            story.extend(_findings(content, styles))
        elif section_type == "profile":
            story.extend(_profile(content, styles))
        elif section_type == "table":
            story.extend(_table(section, content, styles))
        else:
            story.extend(_object(section, content, styles))

    if report.get("disclaimer"):
        story.append(PageBreak())
        story.append(Paragraph("Important", styles["heading"]))
        story.append(Paragraph(report["disclaimer"], styles["small"]))

    document.build(story)
    return buffer.getvalue()


def _object(section: dict[str, Any], content: dict[str, Any], styles) -> list[Any]:  # noqa: ANN001
    labels = section.get("fields") or {}
    flowables: list[Any] = []
    for key, value in content.items():
        if key == "counts":
            continue
        label = labels.get(key, key.replace("_", " ").title())
        if isinstance(value, list):
            flowables.append(Paragraph(f"<b>{label}:</b>", styles["body"]))
            for item in value:
                flowables.append(Paragraph(f"• {item}", styles["body"]))
        else:
            flowables.append(Paragraph(f"<b>{label}:</b> {value}", styles["body"]))
    return flowables


def _grid(data: list[list[Any]]) -> Table:
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _table(section: dict[str, Any], rows: list[dict[str, Any]], styles) -> list[Any]:  # noqa: ANN001
    columns = section.get("columns") or []
    if not columns or not rows:
        return [Paragraph("Nothing to report in this section.", styles["body"])]

    data = [[Paragraph(f"<b>{c.get('title', c['key'])}</b>", styles["cell"]) for c in columns]]
    for row in rows:
        data.append(
            [Paragraph(str(row.get(column["key"], "")), styles["cell"]) for column in columns]
        )
    return [_grid(data), Spacer(1, 6)]


def _profile(content: dict[str, Any], styles) -> list[Any]:  # noqa: ANN001
    data = [
        [Paragraph(f"<b>{title}</b>", styles["cell"]) for title in ("Field", "Value", "Status", "Sources")]
    ]
    for name, field in content.items():
        candidates = field.get("candidates") or []
        value = " / ".join(candidates) if candidates else str(field.get("value", ""))
        sources = "; ".join(
            f"{source['document']} p{source['page']}" for source in field.get("sources", [])
        )
        data.append(
            [
                Paragraph(name.replace("_", " ").title(), styles["cell"]),
                Paragraph(value, styles["cell"]),
                Paragraph(str(field.get("status", "")), styles["cell"]),
                Paragraph(sources, styles["cell"]),
            ]
        )
    return [_grid(data), Spacer(1, 6)]


def _findings(rows: list[dict[str, Any]], styles) -> list[Any]:  # noqa: ANN001
    flowables: list[Any] = []
    for finding in rows:
        colour = SEVERITY_COLOURS.get(str(finding.get("severity")), colors.black)
        block: list[Any] = [
            Paragraph(
                f'<font color="{colour.hexval()}"><b>{finding["id"]} — {finding["severity"]} — '
                f'{finding["type"].replace("_", " ").title()}</b></font>',
                styles["body"],
            )
        ]

        for side in ("1", "2"):
            source = finding.get(f"document_{side}")
            if source and source != "NOT_AVAILABLE":
                page = finding.get(f"page_{side}") or 0
                page_text = f", page {page}" if page else ""
                block.append(
                    Paragraph(
                        f"• <b>{source}</b>{page_text}: {finding.get(f'value_{side}', '')}",
                        styles["body"],
                    )
                )

        if finding.get("explanation"):
            block.append(Paragraph(finding["explanation"], styles["body"]))
        block.append(
            Paragraph(
                f"Status: {finding.get('status')} | Confidence: {finding.get('confidence')} | "
                f"Evidence verified: {'yes' if finding.get('verified') else 'no'} | "
                f"Rule: {finding.get('rule_id')}",
                styles["small"],
            )
        )
        block.append(
            Paragraph(f"<b>Recommended action:</b> {finding.get('recommended_action', '')}",
                      styles["body"])
        )
        block.append(Spacer(1, 8))
        flowables.append(KeepTogether(block))
    return flowables

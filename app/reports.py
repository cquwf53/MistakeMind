from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont


def markdown_to_pdf_bytes(markdown: str) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42)
    base_styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "CNNormal",
        parent=base_styles["Normal"],
        fontName="STSong-Light",
        fontSize=10.5,
        leading=16,
        spaceAfter=6,
    )
    title = ParagraphStyle(
        "CNTitle",
        parent=normal,
        fontSize=18,
        leading=24,
        spaceAfter=14,
    )
    heading = ParagraphStyle(
        "CNHeading",
        parent=normal,
        fontSize=13,
        leading=20,
        spaceBefore=8,
        spaceAfter=8,
    )
    story = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 6))
        elif line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), title))
        elif line.startswith("## "):
            story.append(Paragraph(escape(line[3:]), heading))
        elif line.startswith("- "):
            story.append(Paragraph("• " + escape(line[2:]), normal))
        else:
            story.append(Paragraph(escape(line), normal))
    document.build(story)
    return buffer.getvalue()


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

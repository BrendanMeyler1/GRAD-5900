"""
utils/pdf.py — Convert markdown resume / cover letter to ATS-safe PDF files.

Uses ReportLab (pure Python, no system libraries required) so it works on
Windows, macOS, and Linux without installing GTK/Pango/Cairo.

Output format:
- Single-column flow (no tables, no text boxes) — parses cleanly in every ATS
- Helvetica for screen clarity; 10–10.5 pt body text
- 0.75 in margins → comfortably fits one page of an early-career resume

Usage:
    from utils.pdf import export_resume_pdf, export_cover_letter_pdf
    pdf_path = export_resume_pdf(app_id="abc123", resume_text="# Jane Smith\\n...")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Inline markdown → ReportLab XML conversion
# ─────────────────────────────────────────────────────────────────────────────

def _html_escape(text: str) -> str:
    """Escape the three characters that break ReportLab's XML parser."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_inline(text: str) -> str:
    """
    Convert markdown inline syntax to ReportLab paragraph XML.

    Handles: **bold**, *italic*, `code`, [link](url) → plain text.
    Always HTML-escapes first to avoid XML parse errors from raw < > & in content.
    """
    text = _html_escape(text)
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic: *text* or _text_ (single, not double)
    text = re.sub(r"\*([^*]+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_([^_]+?)_", r"<i>\1</i>", text)
    # Inline code
    text = re.sub(r"`([^`]+?)`", r'<font name="Courier">\1</font>', text)
    # Markdown links → just the display text (ATS doesn't follow links anyway)
    text = re.sub(r"\[([^\]]+?)\]\([^)]+?\)", r"\1", text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# ReportLab style definitions
# ─────────────────────────────────────────────────────────────────────────────

def _build_styles() -> dict[str, Any]:
    """
    Build all ParagraphStyles used in resume / cover-letter rendering.

    Returns a dict keyed by name.  Imported lazily so the module can be
    imported even when reportlab is not installed (the error surfaces at
    call time, not import time).
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    return {
        # ── Resume styles ──────────────────────────────────────────────────
        "h1": ParagraphStyle(
            "h1",
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            alignment=TA_CENTER,
            spaceBefore=0,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "contact",
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            alignment=TA_CENTER,
            spaceBefore=0,
            spaceAfter=5,
            textColor=colors.HexColor("#333333"),
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            alignment=TA_LEFT,
            spaceBefore=7,
            spaceAfter=1,
            textColor=colors.black,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=1,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName="Helvetica",
            fontSize=10,
            leading=12,
            leftIndent=10,
            firstLineIndent=0,
            spaceBefore=1,
            spaceAfter=1,
        ),
        "normal": ParagraphStyle(
            "normal",
            fontName="Helvetica",
            fontSize=10,
            leading=12.5,
            spaceBefore=2,
            spaceAfter=2,
        ),
        # ── Cover letter styles ────────────────────────────────────────────
        "cl_body": ParagraphStyle(
            "cl_body",
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            spaceBefore=0,
            spaceAfter=10,
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Markdown → ReportLab story parser
# ─────────────────────────────────────────────────────────────────────────────

def _md_to_story(markdown_text: str, styles: dict[str, Any]) -> list:
    """
    Parse a markdown resume into a list of ReportLab flowables (the "story").

    Recognises:
        # Name          → h1, centred
        <contact line>  → contact style (centred, 9pt) — the first non-blank
                          line that immediately follows a # heading
        ## SECTION      → h2 + horizontal rule
        ### Job Title   → h3
        - bullet        → bullet paragraph with • prefix
        --- / ===       → decorative separator, silently dropped
        blank line      → small vertical spacer
        everything else → normal paragraph
    """
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable, Paragraph, Spacer

    story: list = []
    lines = markdown_text.splitlines()

    # State: did we just see a # heading?  Next non-blank line is contact info.
    after_h1 = False

    for raw_line in lines:
        line = raw_line.rstrip()

        # ── H1 (name) ─────────────────────────────────────────────────────
        if line.startswith("# "):
            text = _convert_inline(line[2:].strip())
            story.append(Paragraph(text, styles["h1"]))
            after_h1 = True
            continue

        # ── H2 (section header) ───────────────────────────────────────────
        if line.startswith("## "):
            after_h1 = False
            text = _convert_inline(line[3:].strip().upper())
            story.append(Paragraph(text, styles["h2"]))
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.5,
                    color=colors.black,
                    spaceAfter=2,
                )
            )
            continue

        # ── H3 (role / company) ───────────────────────────────────────────
        if line.startswith("### "):
            after_h1 = False
            text = _convert_inline(line[4:].strip())
            story.append(Paragraph(text, styles["h3"]))
            continue

        # ── Bullet ────────────────────────────────────────────────────────
        if line.startswith("- ") or line.startswith("* "):
            after_h1 = False
            text = _convert_inline(line[2:].strip())
            story.append(Paragraph(f"\u2022\u00a0{text}", styles["bullet"]))
            continue

        # ── Decorative separators — drop silently ─────────────────────────
        if re.match(r"^-{3,}$", line) or re.match(r"^={3,}$", line):
            continue

        # ── Blank line ────────────────────────────────────────────────────
        if line.strip() == "":
            if not after_h1:
                story.append(Spacer(1, 2))
            continue

        # ── Everything else ───────────────────────────────────────────────
        text = _convert_inline(line.strip())
        if not text:
            continue

        if after_h1:
            # The line immediately after the candidate's name is the contact line
            story.append(Paragraph(text, styles["contact"]))
            after_h1 = False
        else:
            story.append(Paragraph(text, styles["normal"]))

    return story


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def markdown_to_pdf(markdown_text: str, output_path: str | Path) -> str:
    """
    Convert a Markdown document to a clean, ATS-safe PDF file.

    Args:
        markdown_text: Resume or cover letter in Markdown format.
        output_path:   Destination file path (parent dirs are created if absent).

    Returns:
        Absolute path to the written PDF.

    Raises:
        RuntimeError: If reportlab is not installed.
        OSError:      If the output path cannot be written.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required for PDF generation. "
            "Install it with:  pip install reportlab"
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title="Resume",
        author="",
    )

    styles = _build_styles()
    story = _md_to_story(markdown_text, styles)

    doc.build(story)

    size_kb = output_path.stat().st_size // 1024
    log.info("pdf.generated", extra={"output": str(output_path), "size_kb": size_kb})
    return str(output_path.absolute())


def export_resume_pdf(
    app_id: str,
    resume_text: str,
    generated_dir: str = "./data/generated",
) -> str:
    """
    Export a tailored resume as a PDF for a specific application.

    Writes to: {generated_dir}/{app_id}/resume.pdf

    Args:
        app_id:        Application ID (used as sub-directory name).
        resume_text:   Tailored resume in Markdown format.
        generated_dir: Base directory for generated artefacts.

    Returns:
        Absolute path to the written PDF.
    """
    output_path = Path(generated_dir) / app_id / "resume.pdf"
    return markdown_to_pdf(resume_text, output_path)


def export_cover_letter_pdf(
    app_id: str,
    cover_letter_text: str,
    generated_dir: str = "./data/generated",
) -> str:
    """
    Export a cover letter as a PDF for a specific application.

    Writes to: {generated_dir}/{app_id}/cover_letter.pdf

    Plain-text paragraphs are wrapped in minimal Markdown so the shared
    renderer produces clean paragraph spacing.

    Args:
        app_id:             Application ID.
        cover_letter_text:  Cover letter as plain text or light Markdown.
        generated_dir:      Base directory for generated artefacts.

    Returns:
        Absolute path to the written PDF.
    """
    # If the text isn't already Markdown, preserve paragraph breaks.
    if not cover_letter_text.lstrip().startswith("#"):
        formatted = "\n\n".join(
            p.strip() for p in cover_letter_text.split("\n\n") if p.strip()
        )
    else:
        formatted = cover_letter_text

    output_path = Path(generated_dir) / app_id / "cover_letter.pdf"
    return markdown_to_pdf(formatted, output_path)

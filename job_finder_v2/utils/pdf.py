"""
utils/pdf.py — Convert markdown resume text to a clean PDF file.

The PDF is used in two ways:
1. Uploaded to the "resume" file field on job application forms.
2. Available for the user to download their tailored resume.

Uses weasyprint to render markdown → HTML → PDF. Output is ATS-friendly:
clean single-column layout, no header/footer, no decorative images.

Usage:
    from utils.pdf import markdown_to_pdf, export_resume_pdf
    path = export_resume_pdf(app_id="abc123", resume_text="# Jane Smith\n...")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Minimal CSS that produces a clean, ATS-safe PDF
_RESUME_CSS = """
@page {
    size: letter;
    margin: 0.75in 0.75in 0.75in 0.75in;
}
body {
    font-family: "Times New Roman", Times, serif;
    font-size: 11pt;
    line-height: 1.4;
    color: #000000;
}
h1 {
    font-size: 16pt;
    font-weight: bold;
    margin-bottom: 2pt;
    text-align: center;
}
h2 {
    font-size: 12pt;
    font-weight: bold;
    margin-top: 10pt;
    margin-bottom: 3pt;
    border-bottom: 1px solid #000000;
    padding-bottom: 1pt;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
}
h3 {
    font-size: 11pt;
    font-weight: bold;
    margin-top: 6pt;
    margin-bottom: 1pt;
}
p {
    margin: 2pt 0;
}
ul {
    margin: 2pt 0 4pt 14pt;
    padding: 0;
}
li {
    margin-bottom: 1pt;
}
a {
    color: #000000;
    text-decoration: none;
}
.contact-line {
    text-align: center;
    font-size: 10pt;
    color: #333333;
    margin-bottom: 6pt;
}
hr {
    border: none;
    border-top: 0.5px solid #000000;
    margin: 4pt 0;
}
"""


def markdown_to_pdf(markdown_text: str, output_path: str | Path) -> str:
    """
    Convert a markdown resume to a clean PDF.

    Args:
        markdown_text: Resume content in Markdown format.
        output_path: Where to write the PDF file. Parent dirs created if needed.

    Returns:
        Absolute path to the written PDF file.

    Raises:
        RuntimeError: If weasyprint or markdown are not installed.
        IOError: If the output path cannot be written.
    """
    try:
        import markdown as md_lib
        from weasyprint import HTML, CSS
    except ImportError as exc:
        raise RuntimeError(
            "weasyprint and markdown are required for PDF generation. "
            "Run: pip install weasyprint markdown"
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert markdown → HTML
    html_body = md_lib.markdown(
        markdown_text,
        extensions=["extra", "nl2br"],
    )

    # Wrap in minimal HTML document
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>{_RESUME_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Render to PDF
    HTML(string=full_html).write_pdf(
        str(output_path),
        stylesheets=[CSS(string=_RESUME_CSS)],
    )

    log.info(
        "Generated resume PDF",
        extra={"output": str(output_path), "size_kb": output_path.stat().st_size // 1024},
    )
    return str(output_path.absolute())


def export_resume_pdf(
    app_id: str,
    resume_text: str,
    generated_dir: str = "./data/generated",
) -> str:
    """
    Export a tailored resume as PDF for a specific application.

    Creates the file at: {generated_dir}/{app_id}/resume.pdf

    Args:
        app_id: Application ID (used for directory naming).
        resume_text: Tailored resume in markdown format.
        generated_dir: Base directory for generated files.

    Returns:
        Absolute path to the PDF file.
    """
    output_path = Path(generated_dir) / app_id / "resume.pdf"
    return markdown_to_pdf(resume_text, output_path)


def export_cover_letter_pdf(
    app_id: str,
    cover_letter_text: str,
    generated_dir: str = "./data/generated",
) -> str:
    """
    Export a cover letter as PDF for a specific application.

    Creates the file at: {generated_dir}/{app_id}/cover_letter.pdf

    Args:
        app_id: Application ID.
        cover_letter_text: Cover letter as plain text or markdown.
        generated_dir: Base directory for generated files.

    Returns:
        Absolute path to the PDF file.
    """
    # Wrap plain text as minimal markdown for consistent rendering
    if not cover_letter_text.startswith("#"):
        formatted = "\n\n".join(
            p.strip() for p in cover_letter_text.split("\n\n") if p.strip()
        )
    else:
        formatted = cover_letter_text

    output_path = Path(generated_dir) / app_id / "cover_letter.pdf"
    return markdown_to_pdf(formatted, output_path)

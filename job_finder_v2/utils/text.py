"""
utils/text.py — Resume text extraction from uploaded files.

Supports PDF and DOCX formats. Returns plain text suitable for
LLM processing (profile extraction, resume tailoring).

Usage:
    from utils.text import extract_resume_text
    text = extract_resume_text("path/to/resume.pdf")
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str | Path) -> str:
    """
    Extract plain text from a PDF file using pypdf.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text with whitespace normalised.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is not a valid PDF or cannot be parsed.
    """
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF extraction. Run: pip install pypdf") from exc

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    try:
        reader = pypdf.PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages.append(text)
        full_text = "\n\n".join(pages)
        log.debug(
            "Extracted PDF text",
            extra={"file": str(path), "pages": len(reader.pages), "chars": len(full_text)},
        )
        return _normalise_whitespace(full_text)
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF {path}: {exc}") from exc


def extract_text_from_docx(file_path: str | Path) -> str:
    """
    Extract plain text from a DOCX file using python-docx.

    Args:
        file_path: Path to the DOCX file.

    Returns:
        Extracted text with paragraphs joined by newlines.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is not a valid DOCX.
    """
    try:
        import docx
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for DOCX extraction. Run: pip install python-docx"
        ) from exc

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX file not found: {path}")

    try:
        doc = docx.Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        full_text = "\n".join(paragraphs)
        log.debug(
            "Extracted DOCX text",
            extra={"file": str(path), "paragraphs": len(paragraphs), "chars": len(full_text)},
        )
        return _normalise_whitespace(full_text)
    except Exception as exc:
        raise ValueError(f"Failed to parse DOCX {path}: {exc}") from exc


def extract_resume_text(file_path: str | Path) -> str:
    """
    Extract plain text from a resume file (PDF or DOCX).

    Dispatches to the correct extractor based on file extension.

    Args:
        file_path: Path to the resume file.

    Returns:
        Extracted resume text.

    Raises:
        ValueError: If the file type is not supported.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext in {".docx", ".doc"}:
        return extract_text_from_docx(path)
    else:
        raise ValueError(
            f"Unsupported file type: {ext!r}. "
            f"Supported formats: .pdf, .docx"
        )


def _normalise_whitespace(text: str) -> str:
    """Collapse excessive whitespace while preserving paragraph structure."""
    import re
    # Collapse 3+ consecutive newlines to double newline
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces to single space on each line
    lines = [re.sub(r" {2,}", " ", line) for line in text.split("\n")]
    return "\n".join(lines).strip()

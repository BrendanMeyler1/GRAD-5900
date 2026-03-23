import re
import warnings
from pathlib import Path
from typing import Iterator
from pypdf import PdfReader
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def load_document_text(file_path: str) -> str:
    """
    Extract text from a PDF, HTML, or TXT file dynamically based on extension.
    """
    return "\n".join(iter_document_sections(file_path)).strip()


def iter_document_sections(file_path: str) -> Iterator[str]:
    """
    Yield document text in reasonably small sections so callers can process a
    large file incrementally instead of materializing all text at once.
    PDFs stream page-by-page; HTML/TXT currently yield a single cleaned block.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        yield from _iter_pdf_sections(file_path)
        return
    if ext in (".html", ".htm"):
        html_text = _load_html(file_path)
        if html_text:
            yield html_text
        return

    text = _load_txt(file_path)
    if text:
        yield text


def _load_pdf(file_path: str) -> str:
    return "\n".join(_iter_pdf_sections(file_path))


def _iter_pdf_sections(file_path: str) -> Iterator[str]:
    reader = PdfReader(file_path)

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        cleaned = text.strip()
        if cleaned:
            yield f"\n[Page {i + 1}]\n{cleaned}"


def _load_html(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    
    soup = BeautifulSoup(html, "lxml")
    
    for tag in soup(["script", "style", "ix:header", "header", "footer", "nav"]):
        tag.decompose()
        
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _load_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def save_uploaded_file(uploaded_file, upload_dir: str = "data/uploads") -> str:
    """
    Save a Streamlit-uploaded file to disk and return its path.
    """
    upload_path = Path(upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)

    file_path = upload_path / uploaded_file.name
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return str(file_path)

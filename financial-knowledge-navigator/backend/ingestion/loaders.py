from pathlib import Path
from pypdf import PdfReader


def load_pdf_text(file_path: str) -> str:
    """
    Extract text from a PDF file.
    """
    reader = PdfReader(file_path)
    pages = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        cleaned = text.strip()
        if cleaned:
            pages.append(f"\n[Page {i + 1}]\n{cleaned}")

    return "\n".join(pages)


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

import hashlib
from typing import List, Dict, Optional


def _document_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def chunk_text(
    text: str,
    source_name: str,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
    document_fingerprint: Optional[str] = None,
    start_chunk_index: int = 0,
) -> List[Dict]:
    """
    Character-based chunker that respects word boundaries.
    Avoids splitting mid-word by scanning for whitespace at both
    the end of a chunk and the start of the next (overlap) chunk.
    """
    if not text.strip():
        return []

    chunks = []
    start = 0
    chunk_id = start_chunk_index
    text_len = len(text)
    document_fingerprint = (document_fingerprint or _document_fingerprint(text))[:12]

    while start < text_len:
        end = min(start + chunk_size, text_len)

        # Snap end backwards to a word boundary (space)
        if end < text_len and text[end] != " ":
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(
                {
                    "chunk_id": f"{source_name}::{document_fingerprint}::chunk_{chunk_id}",
                    "source": source_name,
                    "text": chunk,
                }
            )
            chunk_id += 1

        if end >= text_len:
            break

        # Compute next start with overlap, snapping forward to a word boundary
        next_start = max(0, end - chunk_overlap)
        if next_start > 0 and next_start < text_len and text[next_start] != " ":
            space_pos = text.find(" ", next_start, end)
            if space_pos != -1:
                next_start = space_pos + 1
            else:
                # No space found in overlap zone; just start at end
                next_start = end
        elif next_start < text_len and text[next_start] == " ":
            next_start += 1  # Skip the space itself

        start = next_start

    return chunks

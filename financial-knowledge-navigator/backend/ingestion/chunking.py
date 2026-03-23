from typing import List, Dict


def chunk_text(
    text: str,
    source_name: str,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
) -> List[Dict]:
    """
    Simple character-based chunker.
    Good enough for starter build; can be upgraded later to token-based chunking.
    """
    if not text.strip():
        return []

    chunks = []
    start = 0
    chunk_id = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(
                {
                    "chunk_id": f"{source_name}::chunk_{chunk_id}",
                    "source": source_name,
                    "text": chunk,
                }
            )
            chunk_id += 1

        if end == len(text):
            break

        start = max(0, end - chunk_overlap)

    return chunks

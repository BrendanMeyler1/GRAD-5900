from typing import List, Dict


def build_retrieved_context_text(retrieved_chunks: List[Dict]) -> str:
    if not retrieved_chunks:
        return "No retrieved context available."

    blocks = []
    for i, chunk in enumerate(retrieved_chunks, start=1):
        blocks.append(
            f"[Retrieved Source {i}] {chunk.get('source', 'Unknown')} | {chunk.get('chunk_id', 'Unknown')}\n"
            f"{chunk.get('text', '')}"
        )
    return "\n\n".join(blocks)

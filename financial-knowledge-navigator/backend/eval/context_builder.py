from typing import List, Dict, Optional


def build_structured_facts_context_text(structured_facts: Optional[List[Dict]] = None) -> str:
    if not structured_facts:
        return "No structured facts available."

    blocks = []
    for i, fact in enumerate(structured_facts, start=1):
        period_text = f" | {fact.get('period')}" if fact.get("period") else ""
        page_text = f" | {fact.get('page_label')}" if fact.get("page_label") else ""
        blocks.append(
            f"[Fact {i}] {fact.get('source_name', 'Unknown')} | {fact.get('metric_label', fact.get('metric_key', 'Metric'))}{period_text}{page_text}\n"
            f"Value: {fact.get('value_text', '')}\n"
            f"Evidence: {fact.get('evidence_text', '')}"
        )
    return "\n\n".join(blocks)


def build_retrieved_context_text(
    retrieved_chunks: List[Dict],
    structured_facts: Optional[List[Dict]] = None,
) -> str:
    if not retrieved_chunks and not structured_facts:
        return "No retrieved context available."

    blocks = []
    if retrieved_chunks:
        for i, chunk in enumerate(retrieved_chunks, start=1):
            blocks.append(
                f"[Retrieved Source {i}] {chunk.get('source', 'Unknown')} | {chunk.get('chunk_id', 'Unknown')}\n"
                f"{chunk.get('text', '')}"
            )
    if structured_facts:
        blocks.append("Structured Facts:\n" + build_structured_facts_context_text(structured_facts))
    return "\n\n".join(blocks)

import re
from typing import List, Dict, Set


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", normalize_text(text))


def token_overlap_score(reference: str, candidate: str) -> float:
    ref_tokens = set(tokenize(reference))
    cand_tokens = set(tokenize(candidate))

    if not ref_tokens:
        return 0.0

    return len(ref_tokens & cand_tokens) / len(ref_tokens)


def source_overlap_score(expected_sources: List[str], retrieved_sources: List[str]) -> float:
    expected = {s.lower() for s in expected_sources}
    retrieved = {s.lower() for s in retrieved_sources}

    if not expected:
        return 0.0

    return len(expected & retrieved) / len(expected)


def entity_coverage_score(expected_entities: List[str], answer_text: str, graph_context_text: str = "") -> float:
    combined = normalize_text(answer_text + " " + graph_context_text)
    expected = [e.strip().lower() for e in expected_entities if e.strip()]

    if not expected:
        return 0.0

    covered = sum(1 for entity in expected if entity.lower() in combined)
    return covered / len(expected)


def relationship_coverage_score(expected_relationships: List[str], graph_context_text: str = "") -> float:
    graph_text = normalize_text(graph_context_text)
    expected = [r.strip().lower() for r in expected_relationships if r.strip()]

    if not expected:
        return 0.0

    covered = sum(1 for rel in expected if rel.lower() in graph_text)
    return covered / len(expected)


def answer_length_score(answer_text: str, min_words: int = 40, max_words: int = 250) -> float:
    words = tokenize(answer_text)
    count = len(words)

    if count < min_words:
        return count / min_words

    if count > max_words:
        return max(0.0, max_words / count)

    return 1.0


def average_scores(score_dict: Dict[str, float]) -> float:
    if not score_dict:
        return 0.0
    return sum(score_dict.values()) / len(score_dict)

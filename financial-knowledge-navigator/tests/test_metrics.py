"""Tests for backend.eval.metrics — all pure functions, no mocks needed."""
from backend.eval.metrics import (
    token_overlap_score,
    source_overlap_score,
    entity_coverage_score,
    relationship_coverage_score,
    answer_length_score,
    average_scores,
)


def test_token_overlap_identical():
    assert token_overlap_score("revenue grew 10%", "revenue grew 10%") == 1.0


def test_token_overlap_partial():
    score = token_overlap_score("revenue grew 10%", "revenue declined")
    assert 0.0 < score < 1.0


def test_token_overlap_no_match():
    assert token_overlap_score("revenue grew", "something else") == 0.0


def test_token_overlap_empty_reference():
    assert token_overlap_score("", "anything") == 0.0


def test_source_overlap_full():
    assert source_overlap_score(["10-K.pdf"], ["10-K.pdf", "earnings.txt"]) == 1.0


def test_source_overlap_none():
    assert source_overlap_score(["10-K.pdf"], ["earnings.txt"]) == 0.0


def test_source_overlap_empty_expected():
    assert source_overlap_score([], ["earnings.txt"]) == 0.0


def test_entity_coverage_all_found():
    assert entity_coverage_score(["Apple", "Revenue"], "Apple reported Revenue growth", "") == 1.0


def test_entity_coverage_partial():
    score = entity_coverage_score(["Apple", "Revenue", "EBITDA"], "Apple Revenue", "")
    assert abs(score - 2 / 3) < 0.01


def test_entity_coverage_empty():
    assert entity_coverage_score([], "anything", "") == 0.0


def test_relationship_coverage_found():
    graph_text = "Apple REPORTED revenue of $100B"
    assert relationship_coverage_score(["reported"], graph_text) == 1.0


def test_relationship_coverage_none():
    assert relationship_coverage_score(["invented"], "Apple revenue") == 0.0


def test_answer_length_within_range():
    words = " ".join(["word"] * 100)
    assert answer_length_score(words) == 1.0


def test_answer_length_too_short():
    words = " ".join(["word"] * 10)
    score = answer_length_score(words, min_words=40)
    assert score < 1.0


def test_answer_length_too_long():
    words = " ".join(["word"] * 500)
    score = answer_length_score(words, max_words=250)
    assert score < 1.0


def test_average_scores():
    assert average_scores({"a": 0.5, "b": 1.0}) == 0.75


def test_average_scores_empty():
    assert average_scores({}) == 0.0

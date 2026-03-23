"""Tests for backend.ingestion.chunking — word-boundary fix."""
from backend.ingestion.chunking import chunk_text


def test_chunk_text_empty():
    assert chunk_text("", "test_source") == []


def test_chunk_text_basic():
    text = "word " * 50  # 250 chars (50 words * 5 chars)
    chunks = chunk_text(text.strip(), "src", chunk_size=100, chunk_overlap=20)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["source"] == "src"
        assert c["chunk_id"].startswith("src::")


def test_no_mid_word_split():
    """Issue #8: chunks should not cut words in half."""
    text = "abcde fghij klmno pqrst uvwxy"  # 5-char words with spaces
    chunks = chunk_text(text, "src", chunk_size=12, chunk_overlap=3)

    for c in chunks:
        words = c["text"].split()
        for word in words:
            # Every word should be a complete 5-letter word
            assert word in ("abcde", "fghij", "klmno", "pqrst", "uvwxy"), (
                f"Found partial word: '{word}'"
            )


def test_single_chunk_no_split():
    text = "short text"
    chunks = chunk_text(text, "src", chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "short text"


def test_chunk_ids_sequential():
    text = "hello world " * 100
    chunks = chunk_text(text.strip(), "doc", chunk_size=50, chunk_overlap=10)
    for i, c in enumerate(chunks):
        assert c["chunk_id"].startswith("doc::")
        assert c["chunk_id"].endswith(f"::chunk_{i}")


def test_chunk_ids_change_when_document_content_changes():
    original = chunk_text("alpha beta gamma delta", "report.pdf", chunk_size=12, chunk_overlap=3)
    updated = chunk_text("alpha beta zeta delta", "report.pdf", chunk_size=12, chunk_overlap=3)

    assert [c["chunk_id"] for c in original] != [c["chunk_id"] for c in updated]

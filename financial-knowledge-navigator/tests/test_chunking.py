from backend.ingestion.chunking import chunk_text

def test_chunk_text_empty():
    assert chunk_text("", "test_source") == []

def test_chunk_text_basic():
    text = "A" * 20
    chunks = chunk_text(text, "test_source", chunk_size=10, chunk_overlap=2)
    
    assert len(chunks) == 3
    assert chunks[0]["text"] == "A" * 10
    assert chunks[1]["text"] == "A" * 10
    assert chunks[2]["text"] == "A" * 4
    
    assert chunks[0]["chunk_id"] == "test_source::chunk_0"
    assert chunks[1]["chunk_id"] == "test_source::chunk_1"

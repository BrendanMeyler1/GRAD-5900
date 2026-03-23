from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import shutil

from backend.retrieval.openai_file_search_store import OpenAIFileSearchStore


class FakeVectorStoreFiles:
    def __init__(self):
        self.upload_calls = []
        self.delete_calls = []

    def upload_and_poll(self, **kwargs):
        self.upload_calls.append(kwargs)
        return SimpleNamespace(id="file_123")

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class FakeVectorStores:
    def __init__(self):
        self.create_calls = 0
        self.retrieve_calls = []
        self.search_calls = []
        self.delete_calls = []
        self.files = FakeVectorStoreFiles()

    def create(self, **kwargs):
        self.create_calls += 1
        return SimpleNamespace(id="vs_created")

    def retrieve(self, vector_store_id):
        self.retrieve_calls.append(vector_store_id)
        return SimpleNamespace(id=vector_store_id)

    def search(self, vector_store_id, **kwargs):
        self.search_calls.append((vector_store_id, kwargs))
        return SimpleNamespace(data=[])

    def delete(self, vector_store_id):
        self.delete_calls.append(vector_store_id)


def _temp_state_path() -> Path:
    base_dir = Path("data") / f"test_openai_file_search_{uuid4().hex}"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "openai_vector_store.json"


def test_openai_file_search_store_initialization_is_lazy():
    state_path = _temp_state_path()
    client = SimpleNamespace(vector_stores=FakeVectorStores())

    try:
        store = OpenAIFileSearchStore(
            state_path=str(state_path),
            client=client,
        )

        assert client.vector_stores.create_calls == 0
        assert store.peek_vector_store_id() is None
    finally:
        shutil.rmtree(state_path.parent, ignore_errors=True)


def test_openai_file_search_store_creates_vector_store_on_first_search():
    state_path = _temp_state_path()
    client = SimpleNamespace(vector_stores=FakeVectorStores())

    try:
        store = OpenAIFileSearchStore(
            state_path=str(state_path),
            client=client,
        )
        results = store.search("How did revenue change?", top_k=3)

        assert results == []
        assert client.vector_stores.create_calls == 1
        assert client.vector_stores.search_calls[0][0] == "vs_created"
        assert store.peek_vector_store_id() == "vs_created"
        assert Path(state_path).exists()
    finally:
        shutil.rmtree(state_path.parent, ignore_errors=True)


def test_openai_file_search_store_uses_cached_state_without_creating_on_init():
    state_path = _temp_state_path()
    state_path.write_text('{"vector_store_id": "vs_cached"}', encoding="utf-8")
    client = SimpleNamespace(vector_stores=FakeVectorStores())

    try:
        store = OpenAIFileSearchStore(
            state_path=str(state_path),
            client=client,
        )

        assert store.peek_vector_store_id() == "vs_cached"
        assert client.vector_stores.create_calls == 0
    finally:
        shutil.rmtree(state_path.parent, ignore_errors=True)

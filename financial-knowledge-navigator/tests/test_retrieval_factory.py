import pytest

from backend.core.config import settings
from backend.retrieval.factory import create_retriever


class FakeHostedRetriever:
    hosted = True
    backend_name = "openai_file_search"

    def __init__(self, state_path=None):
        self.state_path = state_path


class FakeLocalRetriever:
    hosted = False
    backend_name = "local_vector"


def test_retrieval_factory_returns_hosted_retriever(monkeypatch):
    import backend.retrieval.factory as factory_module

    monkeypatch.setattr(settings, "retrieval_backend", "openai_file_search")
    monkeypatch.setattr(factory_module, "OpenAIFileSearchStore", FakeHostedRetriever)

    retriever = create_retriever(state_path="tests/openai_vector_store_state.json")

    assert isinstance(retriever, FakeHostedRetriever)
    assert retriever.state_path == "tests/openai_vector_store_state.json"


def test_retrieval_factory_returns_local_vector_retriever(monkeypatch):
    import backend.retrieval.factory as factory_module

    monkeypatch.setattr(settings, "retrieval_backend", "local_vector")
    monkeypatch.setattr(factory_module, "VectorStore", FakeLocalRetriever)

    retriever = create_retriever()

    assert isinstance(retriever, FakeLocalRetriever)


def test_retrieval_factory_rejects_unknown_backend():
    with pytest.raises(ValueError):
        create_retriever(backend="mystery")

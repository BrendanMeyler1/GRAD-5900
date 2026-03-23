from typing import Optional

from backend.core.config import settings
from backend.retrieval.base import Retriever
from backend.retrieval.openai_file_search_store import OpenAIFileSearchStore
from backend.retrieval.vector_store import VectorStore


def create_retriever(
    backend: Optional[str] = None,
    **overrides,
) -> Retriever:
    """
    Create the configured retrieval backend.

    OpenAI file search remains the default production backend, while the
    legacy local Qdrant-backed retriever stays available for experimentation
    and offline comparisons.
    """

    selected_backend = (
        backend or settings.retrieval_backend or "openai_file_search"
    ).strip().lower()

    if selected_backend in {"openai_file_search", "file_search", "hosted"}:
        state_path = overrides.get("state_path")
        return OpenAIFileSearchStore(state_path=state_path)

    if selected_backend in {"local_vector", "qdrant", "qdrant_vector"}:
        return VectorStore()

    raise ValueError(
        "Unsupported RETRIEVAL_BACKEND "
        f"'{selected_backend}'. Expected 'openai_file_search' or 'local_vector'."
    )

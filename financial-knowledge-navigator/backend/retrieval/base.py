from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class Retriever(ABC):
    """
    Base interface for retrieval backends.

    The current app uses OpenAI-hosted file search in production, but this
    interface keeps the pipeline open to local and hybrid backends for
    experimentation.
    """

    hosted: bool = False
    backend_name: str = "retriever"

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 5,
        file_hash: Optional[str] = None,
    ) -> List[Dict]:
        """
        Return ranked retrieval results in the common chunk-shaped format.
        """

    def upload_document(
        self,
        file_path: str,
        source_name: str,
        file_hash: str,
    ) -> Dict[str, str]:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support direct document upload."
        )

    def delete_vector_store_file(self, vector_store_file_id: str) -> None:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support deleting hosted vector-store files."
        )

    def delete_source(
        self,
        source_name: str,
        file_ids: Optional[List[str]] = None,
    ) -> int:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support source deletion."
        )

    def reset_store(self) -> None:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support store reset."
        )

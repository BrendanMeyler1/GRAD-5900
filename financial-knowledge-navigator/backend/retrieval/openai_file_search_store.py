import json
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.clients import openai_client
from backend.core.config import settings
from backend.retrieval.base import Retriever


class OpenAIFileSearchStore(Retriever):
    backend_name = "openai_file_search"

    def __init__(self, state_path: Optional[str] = None, client=None):
        self.client = client or openai_client
        self.state_path = Path(state_path or Path(settings.artifacts_dir) / "openai_vector_store.json")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._vector_store_id = self._read_state().get("vector_store_id")
        self.hosted = True

    def _read_state(self) -> Dict:
        if not self.state_path.exists():
            return {}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_state(self, payload: Dict) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def peek_vector_store_id(self) -> Optional[str]:
        return self._vector_store_id or self._read_state().get("vector_store_id")

    def _ensure_vector_store_id(self, validate_existing: bool = True) -> str:
        state = self._read_state()
        vector_store_id = self._vector_store_id or state.get("vector_store_id")
        if vector_store_id:
            self._vector_store_id = vector_store_id
            if not validate_existing:
                return vector_store_id
            try:
                self.client.vector_stores.retrieve(vector_store_id)
                return vector_store_id
            except Exception:
                pass

        vector_store = self.client.vector_stores.create(
            name="Financial Knowledge Navigator",
        )
        state["vector_store_id"] = vector_store.id
        self._write_state(state)
        self._vector_store_id = vector_store.id
        return vector_store.id

    def upload_document(self, file_path: str, source_name: str, file_hash: str) -> Dict[str, str]:
        vector_store_id = self._ensure_vector_store_id()
        with open(file_path, "rb") as f:
            vector_store_file = self.client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=f,
                attributes={
                    "source_name": source_name,
                    "file_hash": file_hash,
                },
            )

        return {
            "vector_store_id": vector_store_id,
            "vector_store_file_id": vector_store_file.id,
        }

    def delete_vector_store_file(self, vector_store_file_id: str) -> None:
        vector_store_id = self._ensure_vector_store_id()
        self.client.vector_stores.files.delete(
            file_id=vector_store_file_id,
            vector_store_id=vector_store_id,
        )

    def delete_source(self, source_name: str, file_ids: Optional[List[str]] = None) -> int:
        removed = 0
        for file_id in file_ids or []:
            self.delete_vector_store_file(file_id)
            removed += 1
        return removed

    def search(self, query: str, top_k: int = 5, file_hash: Optional[str] = None) -> List[Dict]:
        vector_store_id = self._ensure_vector_store_id()
        filters = None
        if file_hash:
            filters = {
                "type": "eq",
                "key": "file_hash",
                "value": file_hash,
            }

        response = self.client.vector_stores.search(
            vector_store_id,
            query=query,
            max_num_results=top_k,
            filters=filters,
            ranking_options={"ranker": "auto"},
            rewrite_query=True,
        )

        results = []
        for index, item in enumerate(response.data):
            content = "\n".join(part.text for part in item.content if getattr(part, "type", "") == "text")
            attributes = item.attributes or {}
            results.append(
                {
                    "score": item.score,
                    "chunk_id": f"{item.file_id}::result_{index}",
                    "source": attributes.get("source_name") or item.filename,
                    "text": content,
                    "file_hash": attributes.get("file_hash"),
                    "vector_store_file_id": item.file_id,
                }
            )
        return results

    def reset_store(self) -> None:
        current_vector_store_id = self.peek_vector_store_id()
        try:
            if current_vector_store_id:
                self.client.vector_stores.delete(current_vector_store_id)
        except Exception:
            pass
        self._vector_store_id = None
        self._write_state({})
        self._vector_store_id = self._ensure_vector_store_id(validate_existing=False)

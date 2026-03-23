from typing import List, Dict, Optional, Set
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.core.clients import openai_client
from backend.core.config import settings
from backend.retrieval.base import Retriever

# Issue #7: Maximum texts per embedding API call to avoid token limits
EMBED_BATCH_SIZE = 100


class VectorStore(Retriever):
    backend_name = "local_vector"

    def __init__(self):
        self.client = openai_client
        self.qdrant = QdrantClient(path=settings.qdrant_path)
        self.collection_name = settings.qdrant_collection
        self.vector_size = 1536
        self.indexed_chunk_ids: Set[str] = set()

        self._ensure_collection()
        self._load_existing_chunk_ids()

    def _recreate_qdrant_client(self) -> None:
        self.qdrant = QdrantClient(path=settings.qdrant_path)
        self.indexed_chunk_ids = set()
        self._ensure_collection()
        self._load_existing_chunk_ids()

    def ensure_open(self) -> None:
        try:
            self.qdrant.get_collections()
        except RuntimeError as exc:
            if "closed" not in str(exc).lower():
                raise
            self._recreate_qdrant_client()

    def _ensure_collection(self) -> None:
        collections = self.qdrant.get_collections().collections
        existing = {c.name for c in collections}

        if self.collection_name not in existing:
            self.qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )

    def _load_existing_chunk_ids(self) -> None:
        """
        Best-effort scan of existing payloads so we do not re-index the same chunk ids.
        """
        try:
            offset = None
            while True:
                points, next_offset = self.qdrant.scroll(
                    collection_name=self.collection_name,
                    with_payload=True,
                    with_vectors=False,
                    limit=256,
                    offset=offset,
                )

                for point in points:
                    payload = point.payload or {}
                    chunk_id = payload.get("chunk_id")
                    if chunk_id:
                        self.indexed_chunk_ids.add(chunk_id)

                if next_offset is None:
                    break
                offset = next_offset
        except Exception:
            # If scroll fails for any reason, we simply proceed without preload.
            self.indexed_chunk_ids = set()

    def _source_filter(self, source_name: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="source",
                    match=models.MatchValue(value=source_name),
                )
            ]
        )

    def _file_hash_filter(self, file_hash: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="file_hash",
                    match=models.MatchValue(value=file_hash),
                )
            ]
        )

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed texts in batches to avoid exceeding API token limits."""
        all_embeddings = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            response = self.client.embeddings.create(
                model=settings.embedding_model,
                input=batch,
            )
            all_embeddings.extend([item.embedding for item in response.data])
        return all_embeddings

    def _embed_chunk_batch(self, chunks: List[Dict]) -> List[models.PointStruct]:
        texts = [chunk["text"] for chunk in chunks]
        response = self.client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )

        points = []
        for chunk, item in zip(chunks, response.data):
            points.append(
                models.PointStruct(
                    id=str(uuid4()),
                    vector=item.embedding,
                    payload={
                        "chunk_id": chunk["chunk_id"],
                        "source": chunk["source"],
                        "text": chunk["text"],
                        "file_hash": chunk.get("file_hash"),
                    },
                )
            )
        return points

    def embed_query(self, query: str) -> List[float]:
        response = self.client.embeddings.create(
            model=settings.embedding_model,
            input=[query],
        )
        return response.data[0].embedding

    def index_chunks(self, chunks: List[Dict]) -> int:
        self.ensure_open()
        if not chunks:
            return 0

        new_chunks = [chunk for chunk in chunks if chunk["chunk_id"] not in self.indexed_chunk_ids]
        if not new_chunks:
            return 0

        indexed_count = 0

        for i in range(0, len(new_chunks), EMBED_BATCH_SIZE):
            batch = new_chunks[i : i + EMBED_BATCH_SIZE]
            points = self._embed_chunk_batch(batch)

            self.qdrant.upsert(
                collection_name=self.collection_name,
                points=points,
            )

            for chunk in batch:
                self.indexed_chunk_ids.add(chunk["chunk_id"])

            indexed_count += len(batch)

        return indexed_count

    def delete_source(self, source_name: str, file_ids=None) -> int:
        self.ensure_open()
        source_filter = self._source_filter(source_name)
        chunk_ids_to_remove = set()
        offset = None

        while True:
            points, next_offset = self.qdrant.scroll(
                collection_name=self.collection_name,
                scroll_filter=source_filter,
                with_payload=True,
                with_vectors=False,
                limit=256,
                offset=offset,
            )

            for point in points:
                payload = point.payload or {}
                chunk_id = payload.get("chunk_id")
                if chunk_id:
                    chunk_ids_to_remove.add(chunk_id)

            if next_offset is None:
                break
            offset = next_offset

        self.qdrant.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(filter=source_filter),
        )
        self.indexed_chunk_ids.difference_update(chunk_ids_to_remove)

        return len(chunk_ids_to_remove)

    def reset_store(self) -> None:
        self.ensure_open()
        try:
            self.qdrant.delete_collection(self.collection_name)
        except Exception:
            pass
        self.indexed_chunk_ids = set()
        self._ensure_collection()

    def search(self, query: str, top_k: int = 5, file_hash: Optional[str] = None) -> List[Dict]:
        self.ensure_open()
        query_vector = self.embed_query(query)
        query_filter = self._file_hash_filter(file_hash) if file_hash else None

        # Use query_points (qdrant-client >= 1.12) with search fallback
        try:
            query_response = self.qdrant.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=top_k,
                query_filter=query_filter,
            )
            results = query_response.points
        except AttributeError:
            # Fallback for older qdrant-client versions that still have .search()
            results = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=top_k,
                query_filter=query_filter,
            )

        return [
            {
                "score": result.score,
                "chunk_id": result.payload.get("chunk_id"),
                "source": result.payload.get("source"),
                "text": result.payload.get("text"),
                "file_hash": result.payload.get("file_hash"),
            }
            for result in results
        ]

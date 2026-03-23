from typing import List, Dict, Set
from uuid import uuid4

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.core.config import settings


class VectorStore:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.qdrant = QdrantClient(path=settings.qdrant_path)
        self.collection_name = settings.qdrant_collection
        self.vector_size = 1536
        self.indexed_chunk_ids: Set[str] = set()

        self._ensure_collection()
        self._load_existing_chunk_ids()

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

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def embed_query(self, query: str) -> List[float]:
        response = self.client.embeddings.create(
            model=settings.embedding_model,
            input=[query],
        )
        return response.data[0].embedding

    def index_chunks(self, chunks: List[Dict]) -> int:
        if not chunks:
            return 0

        new_chunks = [chunk for chunk in chunks if chunk["chunk_id"] not in self.indexed_chunk_ids]
        if not new_chunks:
            return 0

        texts = [chunk["text"] for chunk in new_chunks]
        embeddings = self.embed_texts(texts)

        points = []
        for chunk, embedding in zip(new_chunks, embeddings):
            points.append(
                models.PointStruct(
                    id=str(uuid4()),
                    vector=embedding,
                    payload={
                        "chunk_id": chunk["chunk_id"],
                        "source": chunk["source"],
                        "text": chunk["text"],
                    },
                )
            )

        self.qdrant.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        for chunk in new_chunks:
            self.indexed_chunk_ids.add(chunk["chunk_id"])

        return len(new_chunks)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        query_vector = self.embed_query(query)

        results = self.qdrant.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
        )

        return [
            {
                "score": result.score,
                "chunk_id": result.payload.get("chunk_id"),
                "source": result.payload.get("source"),
                "text": result.payload.get("text"),
            }
            for result in results
        ]

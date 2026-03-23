import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "financial_docs")
    qdrant_path: str = os.getenv("QDRANT_PATH", "data/qdrant")
    artifacts_dir: str = os.getenv("ARTIFACTS_DIR", "data/artifacts")
    top_k: int = int(os.getenv("TOP_K", "5"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "700"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "120"))


settings = Settings()

if not settings.openai_api_key:
    raise ValueError("OPENAI_API_KEY is missing. Add it to your .env file.")

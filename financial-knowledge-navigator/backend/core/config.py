import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    retrieval_backend: str = os.getenv("RETRIEVAL_BACKEND", "openai_file_search")
    graph_backend: str = os.getenv("GRAPH_BACKEND", "sqlite")
    evaluation_backend: str = os.getenv("EVALUATION_BACKEND", "auto")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "financial_docs")
    qdrant_path: str = os.getenv("QDRANT_PATH", "data/qdrant")
    artifacts_dir: str = os.getenv("ARTIFACTS_DIR", "data/artifacts")
    graph_db_path: str = os.getenv("GRAPH_DB_PATH", "data/graph/knowledge_graph.db")
    facts_db_path: str = os.getenv("FACTS_DB_PATH", "data/structured/financial_facts.db")
    online_eval_db_path: str = os.getenv("ONLINE_EVAL_DB_PATH", "data/telemetry/online_eval.db")
    release_workflow_db_path: str = os.getenv("RELEASE_WORKFLOW_DB_PATH", "data/telemetry/release_workflow.db")
    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_username: str = os.getenv("NEO4J_USERNAME", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    top_k: int = int(os.getenv("TOP_K", "5"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "700"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "120"))
    deploy_min_combined_overall: float = float(os.getenv("DEPLOY_MIN_COMBINED_OVERALL", "0.60"))
    deploy_min_ragas_overall: float = float(os.getenv("DEPLOY_MIN_RAGAS_OVERALL", "0.55"))
    deploy_min_online_runs: int = int(os.getenv("DEPLOY_MIN_ONLINE_RUNS", "5"))
    deploy_min_feedback_count: int = int(os.getenv("DEPLOY_MIN_FEEDBACK_COUNT", "2"))
    deploy_min_positive_rate: float = float(os.getenv("DEPLOY_MIN_POSITIVE_RATE", "0.60"))
    deploy_max_avg_latency_ms: float = float(os.getenv("DEPLOY_MAX_AVG_LATENCY_MS", "5000"))


settings = Settings()

if not settings.openai_api_key:
    raise ValueError("OPENAI_API_KEY is missing. Add it to your .env file.")

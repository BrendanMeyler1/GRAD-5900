import sys
import hashlib
import time
import gc
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
from streamlit_agraph import agraph

# Allow imports from project root
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.core.config import settings
from backend.core.cache import ArtifactCache
from backend.core.conversation_history import ConversationHistoryManager
from backend.core.query_cache import QueryResultCache
from backend.core.text_rendering import escape_streamlit_markdown
from backend.ingestion.chunking import chunk_text
from backend.ingestion.loaders import iter_document_sections, save_uploaded_file
from backend.retrieval.bm25_store import BM25Store
from backend.retrieval.factory import create_retriever
from backend.retrieval.hybrid_search import HybridSearcher
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.refined_answer_generator import RefinedAnswerGenerator
from backend.graph.extractor import FinancialGraphExtractor
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.factory import create_graph_store
from backend.graph.background_jobs import BackgroundGraphJobRunner
from backend.graph.preview_jobs import GraphPreviewJobManager
from backend.graph.visualization import (
    build_agraph_elements,
    default_graph_config,
    get_graph_legend_items,
    summarize_graph_relationships,
)
from backend.graph.query_graph import QueryGraphLinker
from backend.graph.graphrag import GraphRAGEngine
from backend.eval.dataset_loader import load_golden_dataset
from backend.eval.deployment_gate import DeploymentGateEvaluator
from backend.eval.release_workflow import ReleaseWorkflowStore
from backend.eval.runner import EvaluationRunner
from backend.eval.ragas_runner import RagasRunner
from backend.eval.judge import LLMJudge
from backend.query_pipeline import QueryPipeline
from backend.eval.reporting import EvaluationReportGenerator
from backend.retrieval.self_corrector import SelfCorrector
from backend.eval.history import EvaluationHistoryManager
from backend.eval.history_reporting import EvaluationHistoryReportGenerator
from backend.core.invalidation import CacheInvalidationManager
from backend.structured.facts_extractor import StructuredFactsExtractor
from backend.structured.facts_store import StructuredFactsStore
from backend.telemetry.online_eval_store import OnlineEvalStore

st.set_page_config(page_title="Financial Knowledge Navigator", layout="wide")

MAX_GRAPH_RENDER_NODES = 180
MAX_GRAPH_RENDER_EDGES = 260

st.markdown(
    """
    <style>
    .st-key-chat_scroll_region {
        height: calc(100vh - 19rem);
        min-height: 26rem;
        max-height: 48rem;
        overflow-y: auto;
        padding-right: 0.5rem;
    }
    .st-key-conversation_list_region {
        max-height: 16rem;
        overflow-y: auto;
        padding-right: 0.35rem;
    }
    .graph-key-item {
        display: flex;
        align-items: center;
        gap: 0.45rem;
        margin: 0.18rem 0;
        font-size: 0.8rem;
        color: #d5dde5;
    }
    .graph-key-swatch {
        width: 0.85rem;
        height: 0.85rem;
        border: 1px solid rgba(255, 255, 255, 0.24);
        background: var(--swatch-color);
        border-radius: 4px;
        flex: 0 0 auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Financial Knowledge Navigator")
st.caption("Persistent-cache build: retrieval, graph reasoning, saved artifacts, query caching, and LLM-as-a-judge evaluation")


def default_retrieval_mode() -> str:
    return "file_search" if getattr(st.session_state.vector_store, "hosted", False) else "hybrid"


# -----------------------------
# Session initialization
# -----------------------------
if "artifact_cache" not in st.session_state:
    st.session_state.artifact_cache = ArtifactCache()

if "query_cache" not in st.session_state:
    st.session_state.query_cache = QueryResultCache()

@st.cache_resource
def get_retriever():
    return create_retriever()

@st.cache_resource
def get_graph_store():
    return create_graph_store()

@st.cache_resource
def get_facts_store():
    return StructuredFactsStore(settings.facts_db_path)

@st.cache_resource
def get_graph_preview_job_manager():
    return GraphPreviewJobManager()

@st.cache_resource
def get_online_eval_store():
    return OnlineEvalStore()

@st.cache_resource
def get_deployment_gate_evaluator():
    return DeploymentGateEvaluator()

@st.cache_resource
def get_release_workflow_store():
    return ReleaseWorkflowStore()

if "vector_store" not in st.session_state:
    st.session_state.vector_store = get_retriever()

if "online_eval_store" not in st.session_state:
    st.session_state.online_eval_store = get_online_eval_store()

if "deployment_gate_evaluator" not in st.session_state:
    st.session_state.deployment_gate_evaluator = get_deployment_gate_evaluator()

if "release_workflow_store" not in st.session_state:
    st.session_state.release_workflow_store = get_release_workflow_store()

if "bm25_store" not in st.session_state:
    st.session_state.bm25_store = BM25Store()

if "hybrid_searcher" not in st.session_state:
    st.session_state.hybrid_searcher = HybridSearcher(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
    )

if "answer_generator" not in st.session_state:
    st.session_state.answer_generator = AnswerGenerator()

if "refined_answer_generator" not in st.session_state:
    st.session_state.refined_answer_generator = RefinedAnswerGenerator()

if "graph_extractor" not in st.session_state:
    st.session_state.graph_extractor = FinancialGraphExtractor()

if "facts_extractor" not in st.session_state:
    st.session_state.facts_extractor = StructuredFactsExtractor()

if "knowledge_graph" not in st.session_state:
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()

if "graph_store" not in st.session_state:
    st.session_state.graph_store = get_graph_store()

if "facts_store" not in st.session_state:
    st.session_state.facts_store = get_facts_store()

if "graph_preview_manager" not in st.session_state:
    st.session_state.graph_preview_manager = get_graph_preview_job_manager()

if "graph_job_runner" not in st.session_state:
    st.session_state.graph_job_runner = BackgroundGraphJobRunner(
        graph_store=st.session_state.graph_store,
        retrieval_store=st.session_state.vector_store,
        graph_extractor=st.session_state.graph_extractor,
        facts_store=st.session_state.facts_store,
    )

if "query_graph_linker" not in st.session_state:
    st.session_state.query_graph_linker = QueryGraphLinker()

if "graphrag_engine" not in st.session_state:
    st.session_state.graphrag_engine = GraphRAGEngine(
        knowledge_graph=st.session_state.knowledge_graph,
        query_graph_linker=st.session_state.query_graph_linker,
    )

if "llm_judge" not in st.session_state:
    st.session_state.llm_judge = LLMJudge(
        query_cache=st.session_state.query_cache,
    )

if "self_corrector" not in st.session_state:
    st.session_state.self_corrector = SelfCorrector()

if "query_pipeline" not in st.session_state:
    st.session_state.query_pipeline = QueryPipeline(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
        hybrid_searcher=st.session_state.hybrid_searcher,
        answer_generator=st.session_state.answer_generator,
        refined_answer_generator=st.session_state.refined_answer_generator,
        graphrag_engine=st.session_state.graphrag_engine,
        query_cache=st.session_state.query_cache,
        self_corrector=st.session_state.self_corrector,
        graph_extractor=st.session_state.graph_extractor,
        facts_extractor=st.session_state.facts_extractor,
        facts_store=st.session_state.facts_store,
        graph_store=st.session_state.graph_store,
    )

if "evaluation_runner" not in st.session_state:
    st.session_state.evaluation_runner = EvaluationRunner(
        query_pipeline=st.session_state.query_pipeline,
        llm_judge=st.session_state.llm_judge,
        ragas_runner=RagasRunner(),
    )

if "report_generator" not in st.session_state:
    st.session_state.report_generator = EvaluationReportGenerator()

if "history_manager" not in st.session_state:
    st.session_state.history_manager = EvaluationHistoryManager()

if "history_report_generator" not in st.session_state:
    st.session_state.history_report_generator = EvaluationHistoryReportGenerator()

if "invalidation_manager" not in st.session_state:
    st.session_state.invalidation_manager = CacheInvalidationManager()

if "conversation_manager" not in st.session_state:
    st.session_state.conversation_manager = ConversationHistoryManager()

if "conversations" not in st.session_state:
    st.session_state.conversations = []

if "current_conversation_id" not in st.session_state:
    st.session_state.current_conversation_id = None

if "last_invalidation_result" not in st.session_state:
    st.session_state.last_invalidation_result = None

if "run_history" not in st.session_state:
    st.session_state.run_history = []

if "selected_run_a" not in st.session_state:
    st.session_state.selected_run_a = None

if "selected_run_b" not in st.session_state:
    st.session_state.selected_run_b = None

if "last_run_comparison" not in st.session_state:
    st.session_state.last_run_comparison = None

if "last_comparison_report_path" not in st.session_state:
    st.session_state.last_comparison_report_path = None

if "indexed_docs" not in st.session_state:
    st.session_state.indexed_docs = []

if "cache_restored" not in st.session_state:
    st.session_state.cache_restored = False

if "last_results" not in st.session_state:
    st.session_state.last_results = []

if "last_vector_results" not in st.session_state:
    st.session_state.last_vector_results = []

if "last_bm25_results" not in st.session_state:
    st.session_state.last_bm25_results = []

if "last_mode" not in st.session_state:
    st.session_state.last_mode = default_retrieval_mode()

if "last_query" not in st.session_state:
    st.session_state.last_query = ""

if "last_preliminary_answer" not in st.session_state:
    st.session_state.last_preliminary_answer = ""

if "last_refined_answer" not in st.session_state:
    st.session_state.last_refined_answer = ""

if "last_facts_context_text" not in st.session_state:
    st.session_state.last_facts_context_text = ""

if "last_graph_context_text" not in st.session_state:
    st.session_state.last_graph_context_text = ""

if "last_graph_context_origin" not in st.session_state:
    st.session_state.last_graph_context_origin = "none"

if "last_highlighted_nodes" not in st.session_state:
    st.session_state.last_highlighted_nodes = []

if "last_subgraph" not in st.session_state:
    st.session_state.last_subgraph = None

if "last_graph_detail_rows" not in st.session_state:
    st.session_state.last_graph_detail_rows = []

if "last_graph_preview_caption" not in st.session_state:
    st.session_state.last_graph_preview_caption = ""

if "last_graph_preview_request_id" not in st.session_state:
    st.session_state.last_graph_preview_request_id = None

if "last_graph_preview_status" not in st.session_state:
    st.session_state.last_graph_preview_status = "idle"

if "last_graph_preview_detail" not in st.session_state:
    st.session_state.last_graph_preview_detail = ""

if "last_graph_preview_submitted_at" not in st.session_state:
    st.session_state.last_graph_preview_submitted_at = None

if "show_persisted_graph_doc" not in st.session_state:
    st.session_state.show_persisted_graph_doc = None

if "last_eval_results" not in st.session_state:
    st.session_state.last_eval_results = None

if "last_cache_hit" not in st.session_state:
    st.session_state.last_cache_hit = False

if "last_report_paths" not in st.session_state:
    st.session_state.last_report_paths = None

if "last_deployment_gate_result" not in st.session_state:
    st.session_state.last_deployment_gate_result = None

if "last_release_report_path" not in st.session_state:
    st.session_state.last_release_report_path = None

# -----------------------------
# Restore cached artifacts once
# -----------------------------
if not st.session_state.cache_restored:
    cached_docs = st.session_state.artifact_cache.list_indexed_documents()
    st.session_state.indexed_docs = [record["source_name"] for record in cached_docs]

    if not getattr(st.session_state.vector_store, "hosted", False):
        for record in cached_docs:
            cached_chunks = st.session_state.artifact_cache.load_chunks(record["file_hash"]) or []
            if cached_chunks:
                st.session_state.bm25_store.index_chunks(cached_chunks)

    st.session_state.cache_restored = True

if not st.session_state.run_history:
    st.session_state.run_history = st.session_state.history_manager.list_runs()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pick_next_uploaded_file(uploaded_files):
    """
    Process one selected document per button click.
    Prefer the first file whose exact content hash is not already cached.
    """
    if not uploaded_files:
        return None, None, 0

    first_cached_match = None
    pending_files = []

    for uploaded_file in uploaded_files:
        file_hash = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
        if st.session_state.artifact_cache.get_document_record(file_hash) is None:
            pending_files.append((uploaded_file, file_hash))
        if first_cached_match is None:
            first_cached_match = (uploaded_file, file_hash)

    if pending_files:
        next_uploaded_file, file_hash = pending_files[0]
        return next_uploaded_file, file_hash, len(pending_files)

    if first_cached_match is None:
        return None, None, 0

    return first_cached_match[0], first_cached_match[1], 0


def clear_graph_preview_state(reset_request_id: bool = True):
    st.session_state.last_subgraph = None
    st.session_state.last_highlighted_nodes = []
    st.session_state.last_graph_detail_rows = []
    st.session_state.last_graph_preview_caption = ""
    st.session_state.last_graph_preview_status = "idle"
    st.session_state.last_graph_preview_detail = ""
    st.session_state.last_graph_preview_submitted_at = None
    if reset_request_id:
        st.session_state.last_graph_preview_request_id = None


def sync_graph_preview_snapshot(request_id: str | None, snapshot: dict | None = None):
    if not request_id:
        clear_graph_preview_state(reset_request_id=True)
        return

    snapshot = snapshot or st.session_state.graph_preview_manager.get_snapshot(request_id)
    if snapshot is None:
        return

    st.session_state.last_graph_preview_request_id = request_id
    st.session_state.last_graph_preview_status = snapshot.get("status", "idle")
    st.session_state.last_graph_preview_detail = snapshot.get("detail", "")
    st.session_state.last_graph_preview_submitted_at = snapshot.get("submitted_at")

    if snapshot.get("status") == "ready" and snapshot.get("result") is not None:
        preview = snapshot["result"]
        st.session_state.last_subgraph = preview["graph"]
        st.session_state.last_highlighted_nodes = preview["highlighted_nodes"]
        st.session_state.last_graph_detail_rows = preview["detail_rows"]
        st.session_state.last_graph_preview_caption = preview["caption"]
        return

    if snapshot.get("status") in {"empty", "failed"}:
        st.session_state.last_subgraph = None
        st.session_state.last_highlighted_nodes = []
        st.session_state.last_graph_detail_rows = []
        st.session_state.last_graph_preview_caption = ""


def queue_graph_preview_for_message(message: dict | None):
    if not message or message.get("role") != "assistant":
        clear_graph_preview_state(reset_request_id=True)
        return

    request_id = message.get("id")
    if not request_id:
        clear_graph_preview_state(reset_request_id=True)
        return

    if st.session_state.last_graph_preview_request_id != request_id:
        clear_graph_preview_state(reset_request_id=False)
        st.session_state.last_graph_preview_request_id = request_id

    snapshot = st.session_state.graph_preview_manager.submit(
        request_id=request_id,
        query=message.get("query", ""),
        results=message.get("selected_results", []),
        mode=message.get("mode", default_retrieval_mode()),
        graph_store=st.session_state.graph_store if graph_store_is_ready() else None,
        graph_extractor=st.session_state.graph_extractor,
        query_graph_linker=st.session_state.query_graph_linker,
        graph_context_origin=message.get("graph_context_origin", "none"),
    )
    sync_graph_preview_snapshot(request_id, snapshot)


def graph_store_is_ready() -> bool:
    checker = getattr(st.session_state.graph_store, "is_configured", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:
        return False


def safe_graph_summary() -> dict:
    if not graph_store_is_ready():
        return {"num_nodes": 0, "num_edges": 0}
    try:
        return st.session_state.graph_store.graph_summary()
    except Exception:
        return {"num_nodes": 0, "num_edges": 0}


def safe_document_has_graph(source_name: str) -> bool:
    if not graph_store_is_ready():
        return False
    try:
        return st.session_state.graph_store.document_has_graph(source_name)
    except Exception:
        return False


def safe_list_graph_jobs() -> list[dict]:
    if not graph_store_is_ready():
        return []
    try:
        return st.session_state.graph_store.list_jobs()
    except Exception:
        return []


def index_document_with_local_retriever(saved_path: str, source_name: str, file_hash: str) -> dict:
    all_chunks = []
    next_chunk_index = 0

    for section in iter_document_sections(saved_path):
        section_chunks = chunk_text(
            section,
            source_name=source_name,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            document_fingerprint=file_hash[:12],
            start_chunk_index=next_chunk_index,
        )
        for chunk in section_chunks:
            chunk["file_hash"] = file_hash
        next_chunk_index += len(section_chunks)
        if not section_chunks:
            continue
        all_chunks.extend(section_chunks)
        st.session_state.vector_store.index_chunks(section_chunks)
        st.session_state.bm25_store.index_chunks(section_chunks)

    st.session_state.artifact_cache.save_chunks(file_hash, all_chunks)
    return {
        "chunk_count": len(all_chunks),
        "vector_store_file_id": None,
        "vector_store_id": None,
    }


def extract_and_store_document_facts(saved_path: str, source_name: str, file_hash: str) -> int:
    extracted_facts = []

    for section_index, section in enumerate(iter_document_sections(saved_path), start=1):
        extracted_facts.extend(
            st.session_state.facts_extractor.extract_from_section(
                section_text=section,
                source_name=source_name,
                file_hash=file_hash,
                section_index=section_index,
            )
        )

    st.session_state.facts_store.replace_document_facts(
        source_name=source_name,
        file_hash=file_hash,
        facts=extracted_facts,
    )
    return len(extracted_facts)


def refresh_conversations():
    st.session_state.conversations = st.session_state.conversation_manager.list_conversations()


def ensure_current_conversation():
    refresh_conversations()

    if st.session_state.current_conversation_id:
        current = st.session_state.conversation_manager.load_conversation(
            st.session_state.current_conversation_id
        )
        if current is not None:
            return current

    if st.session_state.conversations:
        st.session_state.current_conversation_id = st.session_state.conversations[0]["id"]
        return st.session_state.conversation_manager.load_conversation(
            st.session_state.current_conversation_id
        )

    conversation = st.session_state.conversation_manager.create_conversation()
    st.session_state.current_conversation_id = conversation["id"]
    refresh_conversations()
    return conversation


def get_current_conversation():
    return ensure_current_conversation()


def set_current_conversation(conversation_id: str):
    st.session_state.current_conversation_id = conversation_id


def start_new_conversation():
    conversation = st.session_state.conversation_manager.create_conversation()
    st.session_state.current_conversation_id = conversation["id"]
    refresh_conversations()
    reset_output_views()


def latest_assistant_message(conversation: dict | None):
    if not conversation:
        return None

    for message in reversed(conversation.get("messages", [])):
        if message.get("role") == "assistant":
            return message
    return None


def source_record_lookup():
    lookup = {}
    for record in st.session_state.artifact_cache.list_indexed_documents():
        lookup[record.get("source_name")] = record
    return lookup


def build_source_entries(results: list[dict]) -> list[dict]:
    lookup = source_record_lookup()
    entries = []
    for index, result in enumerate(results, start=1):
        record = lookup.get(result.get("source", ""))
        entry = {
            "label": f"Source {index}",
            "anchor_id": f"source-{index}",
            "source_name": result.get("source", "Unknown"),
            "chunk_id": result.get("chunk_id", "Unknown"),
            "text": result.get("text", ""),
            "saved_path": record.get("saved_path") if record else None,
            "score": result.get("score"),
            "rrf_score": result.get("rrf_score"),
            "vector_score": result.get("vector_score"),
            "bm25_score": result.get("bm25_score"),
            "fact_rerank_score": result.get("fact_rerank_score"),
            "fact_match_labels": result.get("fact_match_labels", []),
            "entry_type": "chunk",
        }
        entries.append(entry)
    return entries


def build_fact_entries(facts: list[dict]) -> list[dict]:
    entries = []
    for index, fact in enumerate(facts, start=1):
        entries.append(
            {
                "label": f"Fact {index}",
                "anchor_id": f"fact-{index}",
                "source_name": fact.get("source_name", "Unknown"),
                "metric_label": fact.get("metric_label", fact.get("metric_key", "Metric")),
                "period": fact.get("period"),
                "page_label": fact.get("page_label"),
                "value_text": fact.get("value_text", ""),
                "text": fact.get("evidence_text", ""),
                "entry_type": "fact",
            }
        )
    return entries


def with_source_links(text: str, reference_entries: list[dict], prefix: str) -> str:
    rendered = escape_streamlit_markdown(text)
    for source in reference_entries:
        rendered = rendered.replace(
            source["label"],
            f"[{source['label']}](#{prefix}-{source['anchor_id']})",
        )
    return rendered


def sync_last_outputs_from_message(message: dict | None):
    if not message:
        reset_output_views()
        return

    st.session_state.last_query = message.get("query", "")
    st.session_state.last_mode = message.get("mode", default_retrieval_mode())
    st.session_state.last_results = message.get("selected_results", [])
    st.session_state.last_vector_results = message.get("vector_results", [])
    st.session_state.last_bm25_results = message.get("bm25_results", [])
    st.session_state.last_preliminary_answer = message.get("preliminary_answer", "")
    st.session_state.last_facts_context_text = message.get("facts_context_text", "")
    st.session_state.last_graph_context_text = message.get("graph_context_text", "")
    st.session_state.last_graph_context_origin = message.get("graph_context_origin", "none")
    st.session_state.last_highlighted_nodes = message.get("matched_nodes", [])
    st.session_state.last_refined_answer = message.get("refined_answer", "")
    st.session_state.last_cache_hit = message.get("cache_hit", False)
    st.session_state.last_was_corrected = message.get("was_corrected", False)
    st.session_state.last_rewritten_query = message.get("rewritten_query")

    queue_graph_preview_for_message(message)


def sync_last_outputs_from_conversation(conversation: dict | None):
    sync_last_outputs_from_message(latest_assistant_message(conversation))


def save_conversation(conversation: dict):
    st.session_state.conversation_manager.save_conversation(conversation)
    refresh_conversations()


def rename_current_conversation(new_title: str):
    conversation = get_current_conversation()
    conversation["title"] = new_title.strip() or "Untitled Chat"
    save_conversation(conversation)


def _format_feedback_label(score: int | None) -> str:
    if score is None:
        return "No feedback yet"
    return "Thumbs up recorded" if score > 0 else "Thumbs down recorded"


def record_message_feedback(message_id: str, score: int, label: str):
    conversation = get_current_conversation()
    updated = False

    for message in conversation.get("messages", []):
        if message.get("id") != message_id or message.get("role") != "assistant":
            continue

        telemetry_run_id = message.get("telemetry_run_id")
        if telemetry_run_id:
            st.session_state.online_eval_store.set_feedback(
                run_id=telemetry_run_id,
                score=score,
                label=label,
            )
        message["feedback_score"] = score
        message["feedback_label"] = label
        message["feedback_updated_at"] = utc_now()
        updated = True
        break

    if updated:
        save_conversation(conversation)
        sync_last_outputs_from_conversation(conversation)


def append_chat_turn(query: str, pipeline_result: dict, mode: str):
    conversation = get_current_conversation()
    indexed_doc_keys = get_indexed_doc_cache_keys()
    assistant_id = hashlib.sha256(f"{query}|{utc_now()}".encode("utf-8")).hexdigest()[:12]
    user_message_id = hashlib.sha256(f"user|{query}|{utc_now()}".encode("utf-8")).hexdigest()[:12]
    source_entries = build_source_entries(pipeline_result["selected_results"])
    fact_entries = build_fact_entries(pipeline_result.get("selected_facts", []))
    reference_entries = [*source_entries, *fact_entries]

    user_message = {
        "id": user_message_id,
        "role": "user",
        "content": query,
        "timestamp": utc_now(),
    }
    telemetry_run_id = st.session_state.online_eval_store.log_run(
        {
            "conversation_id": conversation.get("id"),
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_id,
            "created_at": utc_now(),
            "query_text": query,
            "mode": mode,
            "retrieval_backend": getattr(st.session_state.vector_store, "backend_name", settings.retrieval_backend),
            "graph_backend": getattr(st.session_state.graph_store, "backend_name", settings.graph_backend),
            "latency_ms": pipeline_result.get("latency_ms"),
            "cache_hit": pipeline_result.get("cache_hit", False),
            "was_corrected": pipeline_result.get("was_corrected", False),
            "fact_rerank_applied": pipeline_result.get("fact_rerank_applied", False),
            "graph_context_origin": pipeline_result.get("graph_context_origin", "none"),
            "indexed_docs": indexed_doc_keys,
            "retrieved_sources": [result.get("source") for result in pipeline_result.get("selected_results", []) if result.get("source")],
            "answer_text": pipeline_result.get("refined_answer", ""),
            "preliminary_answer": pipeline_result.get("preliminary_answer", ""),
            "refined_answer": pipeline_result.get("refined_answer", ""),
            "retrieved_context_text": pipeline_result.get("retrieved_context_text", ""),
            "graph_context_text": pipeline_result.get("graph_context_text", ""),
            "facts_context_text": pipeline_result.get("facts_context_text", ""),
        }
    )
    assistant_message = {
        "id": assistant_id,
        "role": "assistant",
        "timestamp": utc_now(),
        "query": query,
        "mode": mode,
        "cache_hit": pipeline_result.get("cache_hit", False),
        "was_corrected": pipeline_result.get("was_corrected", False),
        "fact_rerank_applied": pipeline_result.get("fact_rerank_applied", False),
        "rewritten_query": pipeline_result.get("rewritten_query"),
        "selected_results": pipeline_result["selected_results"],
        "selected_facts": pipeline_result.get("selected_facts", []),
        "vector_results": pipeline_result["vector_results"],
        "bm25_results": pipeline_result["bm25_results"],
        "preliminary_answer": pipeline_result["preliminary_answer"],
        "refined_answer": pipeline_result["refined_answer"],
        "facts_context_text": pipeline_result.get("facts_context_text", ""),
        "graph_context_text": pipeline_result["graph_context_text"],
        "graph_context_origin": pipeline_result.get("graph_context_origin", "none"),
        "matched_nodes": pipeline_result["matched_nodes"],
        "source_entries": source_entries,
        "fact_entries": fact_entries,
        "reference_entries": reference_entries,
        "latency_ms": pipeline_result.get("latency_ms"),
        "telemetry_run_id": telemetry_run_id,
        "feedback_score": None,
        "feedback_label": None,
    }

    conversation.setdefault("messages", [])
    conversation["messages"].extend([user_message, assistant_message])

    if conversation.get("title", "New Chat") in ("New Chat", "Untitled Chat"):
        conversation["title"] = query.strip()[:60] or "Untitled Chat"

    save_conversation(conversation)
    sync_last_outputs_from_message(assistant_message)


def render_source_entry(source: dict, prefix: str):
    st.markdown(f"<div id='{prefix}-{source['anchor_id']}'></div>", unsafe_allow_html=True)
    st.markdown(f"**{source['label']}**  ")
    if source.get("entry_type") == "fact":
        caption_parts = [
            source.get("source_name"),
            source.get("metric_label"),
            source.get("period"),
            source.get("page_label"),
            source.get("value_text"),
        ]
        st.caption(" | ".join(part for part in caption_parts if part))
        st.markdown(escape_streamlit_markdown(source.get("text", "")))
        return

    score_parts = []
    if source.get("rrf_score") is not None:
        score_parts.append(f"rrf={source['rrf_score']:.4f}")
    if source.get("vector_score") is not None:
        score_parts.append(f"vector={source['vector_score']:.4f}")
    if source.get("bm25_score") is not None:
        score_parts.append(f"bm25={source['bm25_score']:.4f}")
    if source.get("score") is not None:
        score_parts.append(f"score={source['score']:.4f}")
    if source.get("fact_rerank_score"):
        score_parts.append(f"fact={source['fact_rerank_score']:.2f}")

    score_text = " | ".join(score_parts)
    st.caption(
        " | ".join(
            part for part in [source["source_name"], source["chunk_id"], score_text] if part
        )
    )
    if source.get("fact_match_labels"):
        st.caption("Matched facts: " + ", ".join(source["fact_match_labels"]))
    if source.get("saved_path"):
        st.code(source["saved_path"], language="text")
    st.markdown(escape_streamlit_markdown(source["text"]))


def render_assistant_message(message: dict):
    prefix = message.get("id", "assistant")
    source_entries = message.get("source_entries") or build_source_entries(
        message.get("selected_results", [])
    )
    fact_entries = message.get("fact_entries") or build_fact_entries(
        message.get("selected_facts", [])
    )
    reference_entries = message.get("reference_entries") or [*source_entries, *fact_entries]
    uses_graph_mode = message.get("mode") == "graphrag"

    if message.get("was_corrected"):
        st.warning(
            f"Original query lacked relevant context. Rewritten to: '{message.get('rewritten_query')}'"
        )
    if message.get("fact_rerank_applied"):
        st.caption("Fact-aware reranking adjusted retrieved chunk order using matched structured facts.")
    if message.get("mode") == "graphrag":
        origin = message.get("graph_context_origin", "none")
        if origin == "persisted_graph":
            st.caption("Graph context source: persisted graph neighborhood.")
        elif origin == "query_local":
            st.caption("Graph context source: temporary query-local graph fallback.")

    st.caption(
        f"Mode: {message.get('mode', 'hybrid')} | "
        f"{'Cache hit' if message.get('cache_hit') else 'Fresh result'}"
    )
    if message.get("latency_ms") is not None:
        st.caption(f"Latency: {float(message.get('latency_ms', 0.0)):.0f} ms")

    telemetry_run_id = message.get("telemetry_run_id")
    if telemetry_run_id:
        feedback_cols = st.columns([1, 1, 3])
        with feedback_cols[0]:
            if st.button("Thumbs Up", key=f"feedback_up_{prefix}", use_container_width=True):
                record_message_feedback(prefix, 1, "helpful")
                st.rerun()
        with feedback_cols[1]:
            if st.button("Thumbs Down", key=f"feedback_down_{prefix}", use_container_width=True):
                record_message_feedback(prefix, -1, "needs_work")
                st.rerun()
        with feedback_cols[2]:
            st.caption(_format_feedback_label(message.get("feedback_score")))

    st.markdown("#### Preliminary Answer")
    st.markdown(with_source_links(message.get("preliminary_answer", ""), reference_entries, prefix))

    st.markdown("---")
    st.markdown("#### Refined Graph-Aware Answer" if uses_graph_mode else "#### Refined Answer")
    st.markdown(with_source_links(message.get("refined_answer", ""), reference_entries, prefix))

    if reference_entries:
        st.markdown("**References used**")
        for source in reference_entries:
            st.markdown(
                f"- [{source['label']}](#{prefix}-{source['anchor_id']}) | {source['source_name']}"
            )

        st.markdown("#### Retrieved Chunk Details")
        for source in source_entries:
            render_source_entry(source, prefix)
        if fact_entries:
            st.markdown("#### Structured Fact Details")
            for fact in fact_entries:
                render_source_entry(fact, prefix)

    if fact_entries:
        with st.expander("Structured Facts", expanded=False):
            facts_text = message.get("facts_context_text") or "No structured facts available."
            st.text(facts_text)

    with st.expander("Graph Context", expanded=False):
        graph_text = message.get("graph_context_text") or "No graph context available."
        st.text(graph_text)

def build_query_pipeline():
    return QueryPipeline(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
        hybrid_searcher=st.session_state.hybrid_searcher,
        answer_generator=st.session_state.answer_generator,
        refined_answer_generator=st.session_state.refined_answer_generator,
        graphrag_engine=st.session_state.graphrag_engine,
        query_cache=st.session_state.query_cache,
        self_corrector=st.session_state.self_corrector,
        graph_extractor=st.session_state.graph_extractor,
        facts_extractor=st.session_state.facts_extractor,
        facts_store=st.session_state.facts_store,
        graph_store=st.session_state.graph_store,
    )

def rebuild_runtime_services():
    st.session_state.hybrid_searcher = HybridSearcher(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
    )
    st.session_state.graph_job_runner = BackgroundGraphJobRunner(
        graph_store=st.session_state.graph_store,
        retrieval_store=st.session_state.vector_store,
        graph_extractor=st.session_state.graph_extractor,
        facts_store=st.session_state.facts_store,
    )
    st.session_state.graphrag_engine = GraphRAGEngine(
        knowledge_graph=st.session_state.knowledge_graph,
        query_graph_linker=st.session_state.query_graph_linker,
    )
    st.session_state.query_pipeline = build_query_pipeline()
    st.session_state.evaluation_runner = EvaluationRunner(
        query_pipeline=st.session_state.query_pipeline,
        llm_judge=st.session_state.llm_judge,
        ragas_runner=RagasRunner(),
    )

def get_indexed_doc_cache_keys():
    return st.session_state.artifact_cache.list_indexed_document_keys()

def rebuild_runtime_state_after_partial_clear():
    """
    Rebuild lightweight runtime-only objects after cache invalidation.
    """
    st.session_state.bm25_store = BM25Store()
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()
    rebuild_runtime_services()

def restore_cached_state_into_memory(reindex_vectors: bool = True):
    """
    Reload runtime state from local metadata.
    Hosted retrieval restores document metadata only; local retrieval also
    rebuilds the in-memory BM25 state and can optionally rebuild local vectors.
    """
    st.session_state.indexed_docs = []
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()
    st.session_state.bm25_store = BM25Store()

    cached_docs = st.session_state.artifact_cache.list_indexed_documents()

    for record in cached_docs:
        st.session_state.indexed_docs.append(record["source_name"])
        if getattr(st.session_state.vector_store, "hosted", False):
            continue

        cached_chunks = st.session_state.artifact_cache.load_chunks(record["file_hash"]) or []
        if cached_chunks:
            st.session_state.bm25_store.index_chunks(cached_chunks)
            if reindex_vectors:
                st.session_state.vector_store.index_chunks(cached_chunks)

    rebuild_runtime_services()

def remove_replaced_source_versions(source_name: str, incoming_file_hash: str) -> int:
    prior_records = [
        record
        for record in st.session_state.artifact_cache.list_document_records_for_source(source_name)
        if record.get("file_hash") != incoming_file_hash
    ]

    if not prior_records:
        return 0

    removed_local_source = False
    for record in prior_records:
        vector_store_file_id = record.get("vector_store_file_id")
        if getattr(st.session_state.vector_store, "hosted", False) and vector_store_file_id:
            try:
                st.session_state.vector_store.delete_vector_store_file(vector_store_file_id)
            except Exception:
                pass
        elif not getattr(st.session_state.vector_store, "hosted", False) and not removed_local_source:
            try:
                st.session_state.vector_store.delete_source(source_name)
                removed_local_source = True
            except Exception:
                pass
        st.session_state.facts_store.delete_document_facts(record["file_hash"])
        st.session_state.artifact_cache.delete_document_artifacts(record["file_hash"])

    if graph_store_is_ready():
        st.session_state.graph_store.replace_document_graph(source_name, [])
    restore_cached_state_into_memory(reindex_vectors=False)
    return len(prior_records)

def reset_output_views():
    st.session_state.last_results = []
    st.session_state.last_vector_results = []
    st.session_state.last_bm25_results = []
    st.session_state.last_mode = default_retrieval_mode()
    st.session_state.last_query = ""
    st.session_state.last_preliminary_answer = ""
    st.session_state.last_refined_answer = ""
    st.session_state.last_facts_context_text = ""
    st.session_state.last_graph_context_text = ""
    st.session_state.last_graph_context_origin = "none"
    st.session_state.last_highlighted_nodes = []
    st.session_state.last_subgraph = None
    st.session_state.last_graph_detail_rows = []
    st.session_state.last_graph_preview_caption = ""
    st.session_state.last_graph_preview_request_id = None
    st.session_state.last_graph_preview_status = "idle"
    st.session_state.last_graph_preview_detail = ""
    st.session_state.last_graph_preview_submitted_at = None
    st.session_state.show_persisted_graph_doc = None
    st.session_state.last_eval_results = None
    st.session_state.last_report_paths = None
    st.session_state.last_run_comparison = None
    st.session_state.last_comparison_report_path = None
    st.session_state.last_cache_hit = False


def graph_preview_status_label(status: str) -> str:
    labels = {
        "idle": "Idle",
        "queued": "Queued",
        "running": "Loading",
        "ready": "Ready",
        "empty": "No Connections",
        "failed": "Failed",
    }
    return labels.get(status, status.title())


def render_graph_status(snapshot: dict | None):
    if not snapshot:
        return

    status = snapshot.get("status", "idle")
    submitted_at = snapshot.get("submitted_at")
    elapsed = f"{max(0, int(time.time() - submitted_at))}s elapsed" if submitted_at else None
    status_line = f"Status: {graph_preview_status_label(status)}"
    if elapsed:
        status_line += f" | {elapsed}"
    st.caption(status_line)

    detail = snapshot.get("detail", "")
    if status in {"queued", "running"} and detail:
        st.info(f"{detail} You can keep using the rest of the app while this loads.")
    elif status == "failed" and detail:
        st.error(detail)
    elif status == "empty" and detail:
        st.caption(detail)


def render_graph_key():
    legend = get_graph_legend_items()
    node_col, edge_col = st.columns(2)

    with node_col:
        st.caption("Node key")
        for item in legend["nodes"]:
            st.markdown(
                (
                    f"<div class='graph-key-item'>"
                    f"<span class='graph-key-swatch' style='--swatch-color:{item['color']};'></span>"
                    f"<span><strong>{item['label']}</strong> ({item['shape']})</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )

    with edge_col:
        st.caption("Edge key")
        for item in legend["edges"]:
            st.markdown(
                (
                    f"<div class='graph-key-item'>"
                    f"<span class='graph-key-swatch' style='--swatch-color:{item['color']};'></span>"
                    f"<span><strong>{item['label']}</strong> ({item['relationship_type']})</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )


@st.fragment(run_every="2s")
def render_graph_panel():
    st.subheader("Knowledge graph")

    current_conversation = get_current_conversation()
    latest_message = latest_assistant_message(current_conversation)
    latest_snapshot = None

    if latest_message:
        queue_graph_preview_for_message(latest_message)
        latest_snapshot = st.session_state.graph_preview_manager.get_snapshot(
            latest_message.get("id")
        )
        sync_graph_preview_snapshot(latest_message.get("id"), latest_snapshot)
    else:
        clear_graph_preview_state(reset_request_id=True)

    render_graph_status(latest_snapshot)

    graph_to_show = None
    highlighted_nodes = []
    detail_rows = []
    relationship_rows = []
    caption_text = ""

    if (
        latest_snapshot
        and latest_snapshot.get("status") == "ready"
        and st.session_state.last_subgraph is not None
        and st.session_state.last_subgraph.number_of_nodes() > 0
    ):
        graph_to_show = st.session_state.last_subgraph
        highlighted_nodes = st.session_state.last_highlighted_nodes
        detail_rows = st.session_state.last_graph_detail_rows
        caption_text = (
            st.session_state.last_graph_preview_caption
            or "Showing graph connections for the latest prompt."
        )
    elif latest_snapshot and latest_snapshot.get("status") in {"queued", "running"}:
        graph_to_show = None
    else:
        selected_graph_doc = st.session_state.get("graph_build_source")
        if graph_store_is_ready() and selected_graph_doc and safe_document_has_graph(selected_graph_doc):
            st.caption(f"A persisted graph is available for `{selected_graph_doc}`.")
            load_selected_graph = (
                (
                    latest_message is None
                    and st.session_state.show_persisted_graph_doc == selected_graph_doc
                )
                or st.button(
                    "Load Selected Document Graph" if latest_message is None else "Show Selected Document Graph Instead",
                    key=f"load_selected_graph_{selected_graph_doc}",
                    use_container_width=True,
                )
            )
            if load_selected_graph:
                st.session_state.show_persisted_graph_doc = selected_graph_doc
                persisted_graph = st.session_state.graph_store.get_document_graph(
                    selected_graph_doc,
                    max_nodes=MAX_GRAPH_RENDER_NODES,
                    max_edges=MAX_GRAPH_RENDER_EDGES,
                )
                if persisted_graph.number_of_nodes() > 0:
                    graph_to_show = persisted_graph
                    detail_rows = st.session_state.graph_store.get_document_node_details(
                        selected_graph_doc,
                        limit=20,
                    )
                    caption_text = (
                        f"Showing the persisted {getattr(st.session_state.graph_store, 'backend_name', 'graph')}"
                        f"-backed graph for `{selected_graph_doc}`."
                    )

    if graph_to_show is None:
        if not graph_store_is_ready():
            st.info("The selected graph backend is not configured, so only prompt-level previews are available.")
        elif latest_message and latest_snapshot and latest_snapshot.get("status") == "empty":
            st.info(
                "No prompt-specific graph neighborhood was found for the latest question yet. "
                "You can still load the selected document graph if you want to inspect the raw persisted graph."
            )
        elif not latest_snapshot or latest_snapshot.get("status") in {"idle", "empty", "failed"}:
            st.info(
                "No graph preview is available yet. Queue a background graph build for an indexed document, "
                "ask a question, or load the selected persisted document graph."
            )
        return

    st.caption(caption_text)
    render_graph_key()
    nodes, edges = build_agraph_elements(
        graph_to_show,
        highlighted_nodes=highlighted_nodes,
    )
    config = default_graph_config()
    agraph(nodes=nodes, edges=edges, config=config)

    st.markdown("---")
    relationship_rows = summarize_graph_relationships(graph_to_show, limit=10)
    st.subheader("Key relationships")
    if relationship_rows:
        for relationship in relationship_rows:
            st.write(
                f"- {relationship['source_label']} {relationship['relationship_type']} "
                f"{relationship['target_label']} [{relationship['source_doc']}]"
            )
    else:
        st.write("No summarized graph relationships available yet.")

    st.markdown("---")
    st.subheader("Graph nodes")
    if detail_rows:
        for detail in detail_rows:
            st.write(f"- {detail['label']} ({detail['entity_type']})")
    else:
        st.write("No graph node details available yet.")


def render_panel_header(title: str, step_label: str | None = None, caption: str | None = None):
    heading = f"{step_label} - {title}" if step_label else title
    st.markdown(f"##### {heading}")
    if caption:
        st.caption(caption)


def render_workflow_summary_panel(indexed_records: list[dict]):
    facts_summary = st.session_state.facts_store.summary()
    graph_ready_count = 0
    if graph_store_is_ready():
        graph_ready_count = sum(
            1
            for record in indexed_records
            if safe_document_has_graph(record["source_name"])
        )

    current_conversation = get_current_conversation()
    latest_message = latest_assistant_message(current_conversation)

    if not indexed_records:
        next_step = "Upload files below, then click `Process Next Document` once for each file."
    elif latest_message is None:
        next_step = "Ask your first question in the center chat. `file_search` is the fastest default."
    elif graph_store_is_ready() and graph_ready_count == 0:
        next_step = "Optional: queue a graph build if you want persisted graph reasoning for future `graphrag` queries."
    else:
        next_step = "You can keep chatting, inspect evidence in the tabs, or run evaluation and release checks."

    with st.container(border=True):
        render_panel_header(
            "Workflow Summary",
            caption="Follow the sections below from top to bottom for the cleanest first-run experience.",
        )
        metric_col_1, metric_col_2, metric_col_3 = st.columns(3)
        with metric_col_1:
            st.metric("Indexed docs", len(indexed_records))
        with metric_col_2:
            st.metric("Graph docs", graph_ready_count)
        with metric_col_3:
            st.metric("Facts", facts_summary["num_facts"])
        st.info(next_step)
        st.caption(
            "Retrieval backend: "
            f"{getattr(st.session_state.vector_store, 'backend_name', settings.retrieval_backend)}"
            " | Graph backend: "
            f"{getattr(st.session_state.graph_store, 'backend_name', settings.graph_backend)}"
            " | Evaluation backend: "
            f"{settings.evaluation_backend}"
        )

current_conversation = ensure_current_conversation()
sync_last_outputs_from_conversation(current_conversation)

left_col, center_col, right_col = st.columns([1, 1.5, 1.6])

with left_col:
    indexed_records = st.session_state.artifact_cache.list_indexed_documents()
    render_workflow_summary_panel(indexed_records)

    with st.container(border=True):
        render_panel_header(
            "Conversations",
            "Step 1",
            "Start a new chat, rename the current one, or reopen prior conversations.",
        )

        if st.button("New Chat", use_container_width=True):
            start_new_conversation()
            st.rerun()

        current_conversation = get_current_conversation()
        rename_value = st.text_input(
            "Chat title",
            value=current_conversation.get("title", "New Chat"),
            key=f"conversation_title_{current_conversation['id']}",
        )
        if st.button("Rename Current Chat", use_container_width=True):
            rename_current_conversation(rename_value)
            st.rerun()

        conversation_list_region = st.container(key="conversation_list_region")
        with conversation_list_region:
            for conversation_meta in st.session_state.conversations:
                label = conversation_meta["title"]
                if conversation_meta["id"] == st.session_state.current_conversation_id:
                    label = f"Current | {label}"
                if st.button(
                    label,
                    key=f"conversation_select_{conversation_meta['id']}",
                    use_container_width=True,
                ):
                    set_current_conversation(conversation_meta["id"])
                    sync_last_outputs_from_conversation(
                        st.session_state.conversation_manager.load_conversation(conversation_meta["id"])
                    )
                    st.rerun()

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Documents",
            "Step 2",
            "Add files, process them one at a time, and keep the indexed document list in view.",
        )

        uploaded_files = st.file_uploader(
            "Upload one or more files (PDF, HTML, TXT)",
            type=["pdf", "txt", "html", "htm"],
            accept_multiple_files=True,
        )

        next_uploaded_file = None
        next_uploaded_file_hash = None
        remaining_pending_files = 0
        if uploaded_files:
            next_uploaded_file, next_uploaded_file_hash, remaining_pending_files = pick_next_uploaded_file(uploaded_files)
            if next_uploaded_file is not None:
                st.caption(
                    f"Each click processes one document to keep memory usage stable. Next up: `{next_uploaded_file.name}`."
                )
                if remaining_pending_files > 1:
                    st.caption(
                        f"{remaining_pending_files - 1} additional selected document(s) remain queued for later clicks."
                    )

        if st.button("Process Next Document", type="primary", use_container_width=True):
            if not uploaded_files:
                st.warning("Upload at least one document first.")
            else:
                total_documents_indexed = 0
                reused_docs = 0
                replaced_docs = 0
                total_facts_extracted = 0

                progress = st.progress(0, text="Starting document ingestion...")
                if next_uploaded_file is None:
                    st.warning("No selected documents are available to process.")
                    st.stop()
                files_to_process = [(next_uploaded_file, next_uploaded_file_hash)]

                for idx, (uploaded_file, precomputed_file_hash) in enumerate(files_to_process, start=1):
                    progress.progress(
                        min(int((idx - 1) / len(files_to_process) * 100), 100),
                        text=f"Processing {uploaded_file.name}...",
                    )

                    saved_path = save_uploaded_file(uploaded_file)
                    file_hash = precomputed_file_hash or st.session_state.artifact_cache.file_sha256(saved_path)
                    existing_record = st.session_state.artifact_cache.get_document_record(file_hash)

                    if existing_record:
                        fact_count = existing_record.get("fact_count")
                        if fact_count is None:
                            progress.progress(
                                min(int(idx / len(files_to_process) * 100), 100),
                                text=f"Backfilling structured facts for {uploaded_file.name}...",
                            )
                            fact_count = extract_and_store_document_facts(
                                saved_path=existing_record.get("saved_path", saved_path),
                                source_name=uploaded_file.name,
                                file_hash=file_hash,
                            )
                            st.session_state.artifact_cache.update_document_fields(
                                file_hash,
                                fact_count=fact_count,
                            )
                            total_facts_extracted += fact_count
                        if uploaded_file.name not in st.session_state.indexed_docs:
                            st.session_state.indexed_docs.append(uploaded_file.name)

                        reused_docs += 1
                        progress.progress(
                            min(int(idx / len(files_to_process) * 100), 100),
                            text=f"Reused cached artifacts for {uploaded_file.name}",
                        )
                        continue

                    replaced_docs += remove_replaced_source_versions(uploaded_file.name, file_hash)

                    if getattr(st.session_state.vector_store, "hosted", False):
                        upload_result = st.session_state.vector_store.upload_document(
                            file_path=saved_path,
                            source_name=uploaded_file.name,
                            file_hash=file_hash,
                        )
                    else:
                        upload_result = index_document_with_local_retriever(
                            saved_path=saved_path,
                            source_name=uploaded_file.name,
                            file_hash=file_hash,
                        )

                    progress.progress(
                        min(int(idx / len(files_to_process) * 100), 100),
                        text=f"Extracting structured facts from {uploaded_file.name}...",
                    )
                    fact_count = extract_and_store_document_facts(
                        saved_path=saved_path,
                        source_name=uploaded_file.name,
                        file_hash=file_hash,
                    )
                    total_facts_extracted += fact_count
                    total_documents_indexed += 1

                    if uploaded_file.name not in st.session_state.indexed_docs:
                        st.session_state.indexed_docs.append(uploaded_file.name)

                    st.session_state.artifact_cache.upsert_document_record(
                        file_hash=file_hash,
                        record={
                            "file_hash": file_hash,
                            "source_name": uploaded_file.name,
                            "saved_path": saved_path,
                            "chunk_count": None,
                            "fact_count": fact_count,
                            "vector_store_file_id": upload_result["vector_store_file_id"],
                            "vector_store_id": upload_result["vector_store_id"],
                        },
                    )

                    progress.progress(
                        min(int(idx / len(files_to_process) * 100), 100),
                        text=f"Indexed and cached {uploaded_file.name}",
                    )

                    gc.collect()

                progress.progress(100, text="Done.")

                if getattr(st.session_state.vector_store, "hosted", False):
                    st.success(
                        f"Done. New hosted documents indexed: {total_documents_indexed}. "
                        f"Cached docs reused: {reused_docs}. "
                        f"Prior versions replaced: {replaced_docs}. "
                        f"Structured facts extracted: {total_facts_extracted}. "
                        "Retrieval now uses hosted OpenAI file search. Graph extraction is optional and background-only."
                    )
                else:
                    st.success(
                        f"Done. New local documents indexed: {total_documents_indexed}. "
                        f"Cached docs reused: {reused_docs}. "
                        f"Prior versions replaced: {replaced_docs}. "
                        f"Structured facts extracted: {total_facts_extracted}. "
                        "Retrieval now uses the local Qdrant/BM25 path. Graph extraction is optional and background-only."
                    )
                if remaining_pending_files > 1:
                    st.info(
                        f"{remaining_pending_files - 1} selected document(s) are still waiting. "
                        "Click `Process Next Document` again to continue."
                    )

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Indexed Documents",
            caption="Review which files are currently available to the retrieval pipeline.",
        )

        indexed_records = st.session_state.artifact_cache.list_indexed_documents()

        if st.session_state.indexed_docs:
            for doc in sorted(set(st.session_state.indexed_docs)):
                st.write(f"- {doc}")
        else:
            st.write("No documents indexed yet.")

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Query Settings",
            "Step 3",
            "Choose how the next prompt should retrieve evidence and whether to allow self-correction.",
        )

        retrieval_options = st.session_state.query_pipeline.supported_modes()
        retrieval_mode = st.radio(
            "Choose retrieval strategy",
            options=retrieval_options,
            horizontal=True,
            index=0,
        )
        if retrieval_mode == "file_search":
            st.caption("Uses OpenAI hosted file search for retrieval, with lightweight structured-fact and graph previews generated from retrieved chunks when available.")
        elif retrieval_mode == "vector":
            st.caption("Uses the local Qdrant vector index.")
        elif retrieval_mode == "hybrid":
            st.caption("Uses local vector retrieval fused with BM25 keyword search.")
        elif retrieval_mode == "bm25":
            st.caption("Uses the local BM25 keyword index only.")
        else:
            if getattr(st.session_state.vector_store, "hosted", False):
                st.caption("Uses hosted file search, then builds a tiny temporary graph only from retrieved chunks.")
            else:
                st.caption("Uses local retrieval, then builds a tiny temporary graph only from retrieved chunks.")

        use_correction = st.checkbox("Enable Self-Correcting Retrieval (CRAG)", value=False)

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Graph Build",
            caption="Persist graph neighborhoods for selected documents only when you need deeper graph reasoning.",
        )
        if not indexed_records:
            st.caption("Index documents first. Graphing now happens later and on demand.")
        elif not graph_store_is_ready():
            st.warning("The selected graph backend is not configured. Graph building is disabled until the backend is available.")
        else:
            graph_ready_count = sum(
                1
                for record in indexed_records
                if safe_document_has_graph(record["source_name"])
            )
            st.caption(
                f"Retrieval is ready for {len(indexed_records)} document(s). "
                f"Persisted graph data exists for {graph_ready_count} document(s)."
            )

            graph_doc_options = [record["source_name"] for record in indexed_records]
            selected_graph_doc = st.selectbox(
                "Choose indexed document",
                options=graph_doc_options,
                key="graph_build_source",
            )
            selected_graph_record = next(
                record for record in indexed_records if record["source_name"] == selected_graph_doc
            )

            if safe_document_has_graph(selected_graph_record["source_name"]):
                st.caption("A persisted graph already exists for this document. Queueing a new job will replace it.")
            else:
                st.caption("No persisted graph exists yet for this document.")

            graph_action_col_1, graph_action_col_2 = st.columns(2)
            with graph_action_col_1:
                if st.button("Queue Background Graph Build", use_container_width=True):
                    job_id = st.session_state.graph_store.queue_job(
                        selected_graph_record["source_name"],
                        selected_graph_record["file_hash"],
                    )
                    started = st.session_state.graph_job_runner.start_background_worker()
                    st.success(
                        f"Queued graph job #{job_id} for {selected_graph_record['source_name']}. "
                        f"{'Background worker started.' if started else 'Background worker was already running.'}"
                    )
            with graph_action_col_2:
                if st.button("Refresh Graph Job Status", use_container_width=True):
                    st.rerun()

            recent_jobs = safe_list_graph_jobs()[:8]
            with st.expander("Recent graph jobs", expanded=False):
                if recent_jobs:
                    for job in recent_jobs:
                        status_line = f"#{job['job_id']} | {job['source_name']} | {job['status']}"
                        if job.get("error"):
                            status_line += f" | {job['error'][:100]}"
                        st.write(status_line)
                else:
                    st.caption("No graph jobs have been queued yet.")

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Data Status",
            "Step 4",
            "Review graph coverage, structured facts, cache counts, and backend details in one place.",
        )
        summary = safe_graph_summary()
        facts_summary = st.session_state.facts_store.summary()
        query_cache_stats = st.session_state.query_cache.get_cache_stats()
        status_col_1, status_col_2 = st.columns(2)
        with status_col_1:
            st.metric("Graph nodes", summary["num_nodes"])
            st.metric("Structured facts", facts_summary["num_facts"])
        with status_col_2:
            st.metric("Graph edges", summary["num_edges"])
            st.metric("Query cache entries", query_cache_stats["num_entries"])

        if not graph_store_is_ready():
            st.caption("The selected graph backend is not configured. Retrieval still works, but persisted graph features are disabled.")
        elif summary["num_nodes"] == 0:
            st.caption("No persisted graph data yet. Retrieval still works, and graph builds can be queued when needed.")

        selected_fact_doc = st.session_state.get("graph_build_source")
        if not selected_fact_doc and indexed_records:
            selected_fact_doc = indexed_records[0]["source_name"]

        with st.expander("Structured fact preview", expanded=False):
            if selected_fact_doc:
                preview_facts = st.session_state.facts_store.list_document_facts(
                    source_name=selected_fact_doc,
                    limit=6,
                )
                if preview_facts:
                    st.caption(f"Sample extracted facts for `{selected_fact_doc}`")
                    for fact in preview_facts:
                        period_label = f" | {fact['period']}" if fact.get("period") else ""
                        page_label = f" | {fact['page_label']}" if fact.get("page_label") else ""
                        st.write(
                            f"- {fact['metric_label']}: {fact['value_text']}{period_label}{page_label}"
                        )
                elif facts_summary["num_facts"] == 0:
                    st.caption("No structured facts extracted yet. Facts are populated during document ingestion.")
                else:
                    st.caption(f"No structured facts stored for `{selected_fact_doc}` yet.")
            else:
                st.caption("Index a document to begin extracting structured financial facts.")

        with st.expander("Backend details and storage paths", expanded=False):
            cached_records = st.session_state.artifact_cache.list_indexed_documents()
            st.write(f"Cached documents: {len(cached_records)}")
            st.write(f"Structured facts stored: {facts_summary['num_facts']}")
            st.write(f"Artifact directory: {settings.artifacts_dir}")
            st.write(f"Facts database: {settings.facts_db_path}")
            st.write(f"Release workflow DB: {settings.release_workflow_db_path}")
            st.write(f"Retrieval backend: {getattr(st.session_state.vector_store, 'backend_name', settings.retrieval_backend)}")
            if getattr(st.session_state.vector_store, "hosted", False):
                hosted_vector_store_id = st.session_state.vector_store.peek_vector_store_id() or "Not initialized yet"
                st.write(f"Hosted vector store: {hosted_vector_store_id}")
            else:
                st.write(f"Qdrant path: {settings.qdrant_path}")
            st.write(f"Graph backend: {getattr(st.session_state.graph_store, 'backend_name', settings.graph_backend)}")
            if getattr(st.session_state.graph_store, "backend_name", settings.graph_backend) == "neo4j":
                st.write(f"Neo4j database: {settings.neo4j_database}")
                st.write(f"Neo4j URI: {settings.neo4j_uri or 'Not configured'}")
            else:
                st.write(f"Graph database: {settings.graph_db_path}")

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Advanced Maintenance",
            caption="Use resets and cleanup actions carefully. Some of them require re-indexing or reprocessing.",
        )

        with st.expander("Open cache and storage controls", expanded=False):
            st.caption("Use these controls carefully. Some actions require rebuilding indices or reprocessing documents.")

            col_a, col_b = st.columns(2)

            with col_a:
                if st.button("Clear Query Cache"):
                    result = st.session_state.invalidation_manager.clear_query_cache()
                    st.session_state.query_cache = QueryResultCache()
                    st.session_state.llm_judge = LLMJudge(query_cache=st.session_state.query_cache)
                    rebuild_runtime_services()
                    reset_output_views()
                    st.session_state.last_invalidation_result = ("Cleared query cache", result)
                    st.rerun()

                if st.button("Clear Eval Results"):
                    result = st.session_state.invalidation_manager.clear_eval_results()
                    st.session_state.run_history = st.session_state.history_manager.list_runs()
                    st.session_state.last_eval_results = None
                    st.session_state.last_deployment_gate_result = None
                    st.session_state.last_invalidation_result = ("Cleared evaluation result files", result)
                    st.rerun()

                if st.button("Clear Reports"):
                    result = st.session_state.invalidation_manager.clear_reports()
                    st.session_state.last_report_paths = None
                    st.session_state.last_comparison_report_path = None
                    st.session_state.last_invalidation_result = ("Cleared generated reports", result)
                    st.rerun()

                if st.button("Clear Online Telemetry"):
                    result = st.session_state.invalidation_manager.clear_online_telemetry()
                    get_online_eval_store.clear()
                    st.session_state.online_eval_store = get_online_eval_store()
                    st.session_state.last_deployment_gate_result = None
                    st.session_state.last_invalidation_result = ("Cleared online telemetry", result)
                    st.rerun()

                if st.button("Clear Release Workflow"):
                    result = st.session_state.invalidation_manager.clear_release_workflow()
                    get_release_workflow_store.clear()
                    st.session_state.release_workflow_store = get_release_workflow_store()
                    st.session_state.last_release_report_path = None
                    st.session_state.last_invalidation_result = ("Cleared release workflow history", result)
                    st.rerun()

            with col_b:
                if st.button("Clear Uploaded Files"):
                    result = st.session_state.invalidation_manager.clear_uploads()
                    st.session_state.last_invalidation_result = ("Cleared uploaded files", result)
                    st.rerun()

                if st.button("Clear Artifact Cache"):
                    result = st.session_state.invalidation_manager.clear_artifact_cache()
                    facts_reset = st.session_state.invalidation_manager.clear_structured_facts()

                    st.session_state.artifact_cache = ArtifactCache()
                    get_facts_store.clear()
                    st.session_state.facts_store = get_facts_store()
                    restore_cached_state_into_memory(reindex_vectors=False)
                    reset_output_views()

                    st.session_state.last_invalidation_result = (
                        "Cleared artifact cache",
                        {**result, **facts_reset},
                    )
                    st.rerun()

                if st.button("Clear Persisted Graph Store"):
                    if graph_store_is_ready():
                        st.session_state.graph_store.clear()
                    reset_output_views()
                    st.session_state.last_invalidation_result = (
                        "Cleared persisted graph store",
                        {"graph_rows_removed": "all"},
                    )
                    st.rerun()

                if st.button("Clear Structured Facts Store"):
                    result = st.session_state.invalidation_manager.clear_structured_facts()
                    get_facts_store.clear()
                    st.session_state.facts_store = get_facts_store()
                    st.session_state.artifact_cache.set_field_for_all_documents("fact_count", 0)
                    st.session_state.last_invalidation_result = (
                        "Cleared structured facts store",
                        result,
                    )
                    st.rerun()

                retrieval_reset_label = (
                    "Reset Hosted Retrieval Store"
                    if getattr(st.session_state.vector_store, "hosted", False)
                    else "Reset Local Retrieval Index"
                )
                if st.button(retrieval_reset_label):
                    if getattr(st.session_state.vector_store, "hosted", False):
                        st.session_state.vector_store.reset_store()
                        result = st.session_state.invalidation_manager.clear_artifact_cache()
                        if graph_store_is_ready():
                            st.session_state.graph_store.clear()
                        facts_reset = st.session_state.invalidation_manager.clear_structured_facts()
                        get_facts_store.clear()
                        st.session_state.facts_store = get_facts_store()
                        st.session_state.artifact_cache = ArtifactCache()
                        restore_cached_state_into_memory(reindex_vectors=False)
                        invalidation_payload = {
                            **result,
                            **facts_reset,
                            "vector_store_id": st.session_state.vector_store.peek_vector_store_id(),
                            "graph_rows_removed": "all",
                        }
                        invalidation_label = "Reset hosted retrieval store"
                    else:
                        result = st.session_state.invalidation_manager.clear_qdrant()
                        get_retriever.clear()
                        st.session_state.vector_store = get_retriever()
                        restore_cached_state_into_memory(reindex_vectors=True)
                        invalidation_payload = result
                        invalidation_label = "Reset local retrieval index"

                    reset_output_views()
                    st.session_state.last_invalidation_result = (
                        invalidation_label,
                        invalidation_payload,
                    )
                    st.rerun()

            st.markdown("---")

            if st.button("Full App Reset", type="primary"):
                result = st.session_state.invalidation_manager.full_reset()
                if getattr(st.session_state.vector_store, "hosted", False):
                    st.session_state.vector_store.reset_store()
                else:
                    get_retriever.clear()
                if graph_store_is_ready():
                    st.session_state.graph_store.clear()
                conversations_removed = st.session_state.conversation_manager.clear()

                st.session_state.artifact_cache = ArtifactCache()
                st.session_state.query_cache = QueryResultCache()
                get_facts_store.clear()
                st.session_state.facts_store = get_facts_store()
                st.session_state.vector_store = get_retriever()
                st.session_state.bm25_store = BM25Store()
                st.session_state.knowledge_graph = FinancialKnowledgeGraph()
                st.session_state.indexed_docs = []
                st.session_state.run_history = []
                st.session_state.conversations = []
                st.session_state.current_conversation_id = None
                st.session_state.last_eval_results = None
                st.session_state.last_report_paths = None
                st.session_state.last_run_comparison = None
                st.session_state.last_comparison_report_path = None
                st.session_state.last_deployment_gate_result = None
                st.session_state.last_release_report_path = None
                get_online_eval_store.clear()
                st.session_state.online_eval_store = get_online_eval_store()
                get_release_workflow_store.clear()
                st.session_state.release_workflow_store = get_release_workflow_store()

                st.session_state.llm_judge = LLMJudge(query_cache=st.session_state.query_cache)
                rebuild_runtime_services()

                reset_output_views()
                invalidation_payload = {
                    **result,
                    "graph_rows_removed": "all",
                    "conversations_removed": conversations_removed,
                }
                if getattr(st.session_state.vector_store, "hosted", False):
                    invalidation_payload["vector_store_id"] = st.session_state.vector_store.peek_vector_store_id()
                st.session_state.last_invalidation_result = ("Completed full local reset", invalidation_payload)
                st.rerun()

            if st.button("Reload Indexed Document Metadata"):
                restore_cached_state_into_memory()
                reset_output_views()
                st.session_state.run_history = st.session_state.history_manager.list_runs()
                st.session_state.last_invalidation_result = (
                    "Reloaded indexed document metadata",
                    {"reloaded_documents": len(st.session_state.indexed_docs)},
                )
                st.rerun()

            st.markdown("---")
            st.markdown("### Cached Query Entries")
            cache_entries = st.session_state.query_cache.list_entries()
            if not cache_entries:
                st.caption("No cached query entries found.")
            else:
                for entry in cache_entries[:20]:
                    label = entry["query"] or entry["key"]
                    st.caption(
                        f"{entry['entry_type']} | {entry['mode'] or 'n/a'} | {label[:80]}"
                    )
                    if st.button(
                        f"Delete Cache Entry {entry['key'][:8]}",
                        key=f"delete_cache_entry_{entry['key']}",
                        use_container_width=True,
                    ):
                        st.session_state.query_cache.delete(entry["key"])
                        st.session_state.last_invalidation_result = (
                            "Deleted cached query entry",
                            {"cache_entry_deleted": entry["key"]},
                        )
                        st.rerun()

        if st.session_state.last_invalidation_result:
            label, result = st.session_state.last_invalidation_result
            st.markdown("### Last maintenance action")
            st.write(label)
            for k, v in result.items():
                st.write(f"- {k}: {v}")

    st.markdown("---")
    with st.container(border=True):
        render_panel_header(
            "Evaluation Tools",
            caption="Run the golden dataset benchmark or export reports from the latest evaluation run.",
        )

        eval_action_col_1, eval_action_col_2 = st.columns(2)
        with eval_action_col_1:
            if st.button("Run Golden Dataset Evaluation", use_container_width=True):
                try:
                    dataset = load_golden_dataset()
                    eval_modes = st.session_state.query_pipeline.supported_modes()
                    mode_label = ", ".join(eval_modes)
                    with st.spinner(f"Running evaluation across {mode_label} with cached pipeline + LLM judge..."):
                        eval_results = st.session_state.evaluation_runner.run_dataset(
                            dataset=dataset,
                            indexed_docs=get_indexed_doc_cache_keys(),
                            modes=eval_modes,
                        )
                        saved_path = st.session_state.evaluation_runner.save_results(eval_results)
                        st.session_state.last_eval_results = eval_results
                        st.session_state.last_deployment_gate_result = None
                        st.session_state.run_history = st.session_state.history_manager.list_runs()
                        st.session_state.last_report_paths = None

                    st.success(f"Evaluation complete. Results saved to: {saved_path}")
                except Exception as e:
                    st.error(f"Evaluation failed: {e}")

        with eval_action_col_2:
            if st.button("Export Evaluation Reports", use_container_width=True):
                if not st.session_state.last_eval_results:
                    st.warning("Run evaluation first.")
                else:
                    try:
                        with st.spinner("Generating markdown, CSV, and JSON reports..."):
                            report_paths = st.session_state.report_generator.export_all(
                                st.session_state.last_eval_results
                            )
                            st.session_state.last_report_paths = report_paths

                        st.success("Evaluation reports exported successfully.")
                    except Exception as e:
                        st.error(f"Report export failed: {e}")

        st.caption("Detailed run history, telemetry, deployment gates, and release workflow remain available in the Evaluation tab below the chat.")

with center_col:
    current_conversation = get_current_conversation()
    latest_message = latest_assistant_message(current_conversation)
    with st.container(border=True):
        render_panel_header(
            "Conversation Workspace",
            caption="Scroll through the full transcript here. The latest result tools stay grouped just below it.",
        )
        st.markdown(
            f"### {escape_streamlit_markdown(current_conversation.get('title', 'New Chat'))}"
        )
        st.caption("Persistent chat history. Use the left panel to switch or rename conversations.")

        chat_scroll_region = st.container(border=True, key="chat_scroll_region")
        with chat_scroll_region:
            if not current_conversation.get("messages", []):
                st.info("Start the conversation below. Your prompts and answers will remain available in this chat.")
            else:
                for message in current_conversation.get("messages", []):
                    with st.chat_message(message.get("role", "assistant")):
                        if message.get("role") == "user":
                            st.markdown(escape_streamlit_markdown(message.get("content", "")))
                        else:
                            render_assistant_message(message)

    prompt = st.chat_input("Ask a question about your uploaded financial documents")
    if prompt:
        if not prompt.strip():
            st.warning("Enter a question first.")
        elif not st.session_state.indexed_docs:
            st.warning("Index at least one document first.")
        else:
            query_started_at = time.perf_counter()
            pipeline_result = st.session_state.query_pipeline.run(
                query=prompt,
                mode=retrieval_mode,
                indexed_docs=get_indexed_doc_cache_keys(),
                top_k=settings.top_k,
                use_cache=True,
                use_correction=use_correction,
            )
            pipeline_result["latency_ms"] = round((time.perf_counter() - query_started_at) * 1000, 2)
            append_chat_turn(prompt, pipeline_result, retrieval_mode)
            st.rerun()

    with st.container(border=True):
        render_panel_header(
            "Latest Result Workspace",
            caption="Inspect the newest answer, evidence, structured facts, graph context, and evaluation outputs here.",
        )
        if st.session_state.last_query:
            if getattr(st.session_state, "last_was_corrected", False):
                st.warning(
                    f"Original query lacked relevant context. Rewritten to: '{st.session_state.last_rewritten_query}'"
                )

            if st.session_state.last_cache_hit:
                st.success("Result loaded from query cache.")
            else:
                st.info("Result generated fresh and saved to query cache.")

        answer_tab, sources_tab, facts_tab, graph_context_tab, eval_tab = st.tabs(
            ["Answer", "Sources", "Structured Facts", "Graph Context", "Evaluation"]
        )

        with answer_tab:
            if latest_message:
                reference_entries = latest_message.get("reference_entries") or [
                    *(latest_message.get("source_entries") or []),
                    *(latest_message.get("fact_entries") or []),
                ]
                st.markdown("### Preliminary Answer")
                st.markdown(
                    with_source_links(
                        latest_message.get("preliminary_answer", "No preliminary answer yet."),
                        reference_entries,
                        latest_message.get("id", "assistant"),
                    )
                )

                st.markdown("---")
                refined_heading = (
                    "### Refined Graph-Aware Answer"
                    if latest_message.get("mode") == "graphrag"
                    else "### Refined Answer"
                )
                st.markdown(refined_heading)
                st.markdown(
                    with_source_links(
                        latest_message.get("refined_answer", "No refined answer yet."),
                        reference_entries,
                        latest_message.get("id", "assistant"),
                    )
                )
            else:
                st.info("Run a query to see answers.")

        with sources_tab:
            st.subheader("Retrieved chunks")

            if latest_message and latest_message.get("source_entries"):
                for source in latest_message["source_entries"]:
                    with st.expander(f"{source['label']} | {source['source_name']}", expanded=False):
                        render_source_entry(source, latest_message.get("id", "assistant"))
            else:
                st.write("No retrieval results yet.")

        with facts_tab:
            st.subheader("Structured facts used")
            if latest_message and latest_message.get("fact_entries"):
                for fact in latest_message["fact_entries"]:
                    with st.expander(f"{fact['label']} | {fact['metric_label']}", expanded=False):
                        render_source_entry(fact, latest_message.get("id", "assistant"))
            else:
                st.info("No structured facts were used for the latest answer.")

        with graph_context_tab:
            if latest_message and latest_message.get("graph_context_text"):
                origin = latest_message.get("graph_context_origin", "none")
                if origin == "persisted_graph":
                    st.caption("Using persisted graph neighborhood expansion for this answer.")
                elif origin == "query_local":
                    st.caption("Using temporary query-local graph fallback for this answer.")
                st.text(latest_message["graph_context_text"])
            else:
                st.info("Run a query to generate graph context.")

        with eval_tab:
            eval_subtab_1, eval_subtab_2, eval_subtab_3, eval_subtab_4, eval_subtab_5 = st.tabs(
                ["Current Results", "Run History", "Run Comparison", "Online Telemetry", "Deployment Gate"]
            )

        with eval_subtab_1:
            if st.session_state.last_eval_results:
                summary = st.session_state.last_eval_results["summary"]

                st.markdown("### Evaluation Summary")
                for mode, mode_summary in summary.items():
                    st.markdown(f"**{mode.upper()}**")
                    st.write(f"Average combined overall: {mode_summary['average_combined_overall']:.3f}")
                    st.write(f"Average heuristic overall: {mode_summary['average_heuristic_overall']:.3f}")
                    st.write(f"Average LLM overall (0-5): {mode_summary['average_llm_overall_0_to_5']:.3f}")
                    st.write(
                        "Average standardized RAG overall: "
                        f"{mode_summary.get('average_ragas_overall', 0.0):.3f}"
                    )
                    st.write(
                        "Standardized RAG backend: "
                        f"{mode_summary.get('ragas_backend') or 'unavailable'}"
                    )
                    st.write(f"Pipeline cache hits: {mode_summary['cache_hits']}")

                    st.markdown("Heuristic metrics:")
                    for metric_name, value in mode_summary["average_heuristic_metrics"].items():
                        st.write(f"- {metric_name}: {value:.3f}")

                    st.markdown("LLM judge metrics:")
                    for metric_name, value in mode_summary["average_llm_metrics"].items():
                        st.write(f"- {metric_name}: {value:.3f}")

                    if mode_summary.get("average_ragas_metrics"):
                        st.markdown("Standardized RAG metrics:")
                        for metric_name, value in mode_summary["average_ragas_metrics"].items():
                            st.write(f"- {metric_name}: {value:.3f}")

                    st.markdown("---")

                if st.session_state.last_report_paths:
                    st.markdown("### Exported Reports")
                    for label, path in st.session_state.last_report_paths.items():
                        st.write(f"- {label}: {path}")
                    st.markdown("---")

                st.markdown("### Per-question judge details")
                for mode, results in st.session_state.last_eval_results["results_by_mode"].items():
                    with st.expander(f"{mode.upper()} detailed results", expanded=False):
                        for result in results:
                            st.markdown(f"**{result['question_id']}** - {result['question']}")
                            st.write(f"Combined overall: {result['combined_overall']:.3f}")
                            st.write(f"Heuristic overall: {result['heuristic_overall']:.3f}")
                            st.write(f"LLM overall (0-5): {result['llm_judge']['scores']['overall']}")
                            if result.get("ragas"):
                                st.write(
                                    "Standardized RAG overall: "
                                    f"{result['ragas']['scores'].get('overall', 0.0):.3f}"
                                )
                                st.write(
                                    "Standardized RAG backend: "
                                    f"{result['ragas'].get('backend', 'unknown')}"
                                )
                            st.write(f"Pipeline cache hit: {result.get('cache_hit', False)}")

                            st.markdown("LLM Judge Scores:")
                            for k, v in result["llm_judge"]["scores"].items():
                                st.write(f"- {k}: {v}")

                            st.markdown("LLM Judge Summary:")
                            st.write(result["llm_judge"]["summary"])

                            if result.get("ragas"):
                                st.markdown("Standardized RAG Metrics:")
                                for k, v in result["ragas"]["scores"].items():
                                    st.write(f"- {k}: {v:.3f}")
                                st.write(result["ragas"].get("summary", ""))

                            st.markdown("Rationales:")
                            for k, v in result["llm_judge"]["rationales"].items():
                                st.write(f"- **{k}**: {v}")

                            st.markdown("---")
            else:
                st.info("Run the golden dataset evaluation to see results.")

        with eval_subtab_2:
            st.markdown("### Saved Evaluation Runs")

            run_history = st.session_state.run_history or []

            if not run_history:
                st.info("No saved evaluation runs found yet.")
            else:
                for run in run_history:
                    best = st.session_state.history_manager.get_best_mode({"summary": run["summary"]})

                    with st.expander(f"{run['file_name']} | {run['timestamp']}", expanded=False):
                        st.write(f"Best mode: {best['mode']} ({best['score']:.3f})" if best["mode"] else "Best mode: N/A")
                        st.write(f"Modes: {', '.join(run['modes'])}")

                        for mode, mode_summary in run["summary"].items():
                            st.markdown(f"**{mode.upper()}**")
                            st.write(f"- Combined overall: {mode_summary.get('average_combined_overall', 0.0):.3f}")
                            st.write(f"- Heuristic overall: {mode_summary.get('average_heuristic_overall', 0.0):.3f}")
                            st.write(f"- LLM overall (0-5): {mode_summary.get('average_llm_overall_0_to_5', 0.0):.3f}")
                            st.write(f"- Standardized RAG overall: {mode_summary.get('average_ragas_overall', 0.0):.3f}")
                            st.write(f"- Cache hits: {mode_summary.get('cache_hits', 0)}")

        with eval_subtab_3:
            st.markdown("### Compare Two Runs")

            run_history = st.session_state.run_history or []
            run_names = [run["file_name"] for run in run_history]

            if len(run_names) < 2:
                st.info("You need at least two saved evaluation runs to compare them.")
            else:
                col_a, col_b = st.columns(2)

                with col_a:
                    selected_a = st.selectbox(
                        "Run A (baseline)",
                        options=run_names,
                        index=1 if len(run_names) > 1 else 0,
                        key="compare_run_a",
                    )

                with col_b:
                    selected_b = st.selectbox(
                        "Run B (comparison)",
                        options=run_names,
                        index=0,
                        key="compare_run_b",
                    )

                compare_col_1, compare_col_2 = st.columns([1, 1])

                with compare_col_1:
                    if st.button("Compare Runs"):
                        run_a = st.session_state.history_manager.load_run(selected_a)
                        run_b = st.session_state.history_manager.load_run(selected_b)

                        if run_a is None or run_b is None:
                            st.error("Could not load one or both selected runs.")
                        else:
                            comparison = st.session_state.history_manager.compare_runs(run_a, run_b)
                            st.session_state.last_run_comparison = {
                                "run_a_name": selected_a,
                                "run_b_name": selected_b,
                                "comparison": comparison,
                            }

                with compare_col_2:
                    if st.button("Export Comparison Report"):
                        if not st.session_state.last_run_comparison:
                            st.warning("Run a comparison first.")
                        else:
                            report_path = st.session_state.history_report_generator.export_comparison_markdown(
                                run_a_name=st.session_state.last_run_comparison["run_a_name"],
                                run_b_name=st.session_state.last_run_comparison["run_b_name"],
                                comparison=st.session_state.last_run_comparison["comparison"],
                            )
                            st.session_state.last_comparison_report_path = report_path
                            st.success(f"Comparison report saved: {report_path}")

                if st.session_state.last_comparison_report_path:
                    st.write(f"Latest comparison report: {st.session_state.last_comparison_report_path}")

                if st.session_state.last_run_comparison:
                    st.markdown(
                        f"### Comparison: {st.session_state.last_run_comparison['run_a_name']} -> {st.session_state.last_run_comparison['run_b_name']}"
                    )

                    comparison = st.session_state.last_run_comparison["comparison"]

                    for mode, row in comparison.items():
                        with st.expander(f"{mode.upper()} comparison", expanded=True):
                            st.write(f"Combined overall delta: {row['average_combined_overall_delta']:.3f}")
                            st.write(f"Heuristic overall delta: {row['average_heuristic_overall_delta']:.3f}")
                            st.write(f"LLM overall delta: {row['average_llm_overall_delta']:.3f}")
                            st.write(f"Standardized RAG overall delta: {row['average_ragas_overall_delta']:.3f}")
                            st.write(f"Cache hits delta: {row['cache_hits_delta']}")

                            st.markdown("**Heuristic metric deltas**")
                            for key, value in row["heuristic_metric_deltas"].items():
                                st.write(f"- {key}: {value:.3f}")

                            st.markdown("**LLM metric deltas**")
                            for key, value in row["llm_metric_deltas"].items():
                                st.write(f"- {key}: {value:.3f}")

                            st.markdown("**Standardized RAG metric deltas**")
                            for key, value in row["ragas_metric_deltas"].items():
                                st.write(f"- {key}: {value:.3f}")

        with eval_subtab_4:
            st.markdown("### Online Run Telemetry")
            telemetry_summary = st.session_state.online_eval_store.summary(limit=250)

            summary_cols = st.columns(4)
            summary_cols[0].metric("Logged runs", telemetry_summary["num_runs"])
            summary_cols[1].metric("Feedback count", telemetry_summary["feedback_count"])
            summary_cols[2].metric("Thumbs up", telemetry_summary["thumbs_up"])
            summary_cols[3].metric("Thumbs down", telemetry_summary["thumbs_down"])

            st.write(f"Feedback coverage: {telemetry_summary['feedback_rate']:.1%}")
            st.write(f"Positive feedback rate: {telemetry_summary['positive_rate']:.1%}")
            st.write(f"Average latency: {telemetry_summary['avg_latency_ms']:.1f} ms")
            st.write(f"Cache hit rate: {telemetry_summary['cache_hit_rate']:.1%}")

            st.markdown("### Telemetry by Mode")
            mode_rows = st.session_state.online_eval_store.summarize_by_mode(limit=250)
            if not mode_rows:
                st.info("No online telemetry has been logged yet.")
            else:
                for row in mode_rows:
                    with st.expander(f"{row['mode'].upper()} telemetry", expanded=False):
                        st.write(f"Runs: {row['num_runs']}")
                        st.write(f"Average latency: {row['avg_latency_ms']:.1f} ms")
                        st.write(f"Cache hit rate: {row['cache_hit_rate']:.1%}")
                        st.write(f"Correction rate: {row['correction_rate']:.1%}")
                        st.write(f"Feedback count: {row['feedback_count']}")
                        st.write(f"Thumbs up: {row['thumbs_up']}")
                        st.write(f"Thumbs down: {row['thumbs_down']}")
                        st.write(f"Positive feedback rate: {row['positive_rate']:.1%}")

            st.markdown("### Recent Feedback")
            feedback_runs = st.session_state.online_eval_store.list_runs(limit=20, feedback_only=True)
            if not feedback_runs:
                st.info("No thumbs-up or thumbs-down feedback has been recorded yet.")
            else:
                for run in feedback_runs:
                    feedback_text = "Thumbs up" if (run["feedback_score"] or 0) > 0 else "Thumbs down"
                    st.markdown(f"**{run['query_text']}**")
                    st.write(
                        f"{feedback_text} | Mode: {run['mode']} | "
                        f"Latency: {float(run['latency_ms'] or 0.0):.1f} ms | "
                        f"Cache hit: {run['cache_hit']}"
                    )
                    if run.get("feedback_label"):
                        st.caption(f"Label: {run['feedback_label']}")
                    if run.get("answer_text"):
                        st.caption(run["answer_text"][:220] + ("..." if len(run["answer_text"]) > 220 else ""))
                    st.markdown("---")

        with eval_subtab_5:
            st.markdown("### Deployment Gate")
            online_summary = st.session_state.online_eval_store.summary(limit=250)
            online_mode_rows = st.session_state.online_eval_store.summarize_by_mode(limit=250)
            gate_result = st.session_state.deployment_gate_evaluator.evaluate(
                offline_eval_results=st.session_state.last_eval_results,
                online_summary=online_summary,
                online_mode_rows=online_mode_rows,
            )
            st.session_state.last_deployment_gate_result = gate_result

            if gate_result["overall_ready"]:
                st.success(
                    "Deployment gate passed. "
                    f"Best candidate mode: {gate_result.get('best_candidate_mode') or 'N/A'}"
                )
            else:
                st.error("Deployment gate not yet satisfied.")

            if gate_result.get("deployable_modes"):
                st.write("Deployable modes: " + ", ".join(gate_result["deployable_modes"]))

            if gate_result.get("blockers"):
                st.markdown("**Current blockers**")
                for blocker in gate_result["blockers"]:
                    st.write(f"- {blocker}")

            st.markdown("### Release Workflow")
            release_note = st.text_input(
                "Release note",
                value="",
                key="release_workflow_note",
                help="Optional note to attach to a saved hold or promotion decision.",
            )
            release_col_1, release_col_2, release_col_3 = st.columns(3)

            with release_col_1:
                if st.button("Record Hold Snapshot"):
                    decision_id = st.session_state.release_workflow_store.record_decision(
                        decision="hold",
                        gate_result=gate_result,
                        offline_eval_results=st.session_state.last_eval_results,
                        online_summary=online_summary,
                        note=release_note,
                        selected_mode=gate_result.get("best_candidate_mode"),
                    )
                    st.session_state.last_release_report_path = None
                    st.success(f"Saved hold snapshot: {decision_id}")

            with release_col_2:
                if st.button("Promote Best Candidate"):
                    if not gate_result["overall_ready"] or not gate_result.get("best_candidate_mode"):
                        st.warning("The deployment gate has not passed yet, so there is no candidate to promote.")
                    else:
                        decision_id = st.session_state.release_workflow_store.record_decision(
                            decision="promote",
                            gate_result=gate_result,
                            offline_eval_results=st.session_state.last_eval_results,
                            online_summary=online_summary,
                            note=release_note,
                            selected_mode=gate_result.get("best_candidate_mode"),
                        )
                        st.session_state.last_release_report_path = None
                        st.success(f"Recorded promotion decision: {decision_id}")

            with release_col_3:
                if st.button("Export Latest Release Report"):
                    latest_decision = st.session_state.release_workflow_store.latest_decision()
                    if latest_decision is None:
                        st.warning("Record at least one release decision first.")
                    else:
                        report_path = st.session_state.release_workflow_store.export_markdown_report(
                            latest_decision["decision_id"]
                        )
                        st.session_state.last_release_report_path = report_path
                        st.success(f"Release report saved: {report_path}")

            if st.session_state.last_release_report_path:
                st.write(f"Latest release report: {st.session_state.last_release_report_path}")

            workflow_summary = st.session_state.release_workflow_store.summary()
            workflow_cols = st.columns(4)
            workflow_cols[0].metric("Decisions", workflow_summary["total_decisions"])
            workflow_cols[1].metric("Promotions", workflow_summary["promotions"])
            workflow_cols[2].metric("Holds", workflow_summary["holds"])
            workflow_cols[3].metric("Rollbacks", workflow_summary["rollbacks"])

            if workflow_summary["latest_decision"]:
                st.caption(
                    "Latest decision: "
                    f"{workflow_summary['latest_decision']} | "
                    f"Mode: {workflow_summary.get('latest_mode') or 'N/A'} | "
                    f"Gate ready: {workflow_summary.get('latest_ready')}"
                )

            st.markdown("### Recent Release Decisions")
            recent_decisions = st.session_state.release_workflow_store.list_decisions(limit=10)
            if not recent_decisions:
                st.info("No release workflow decisions have been recorded yet.")
            else:
                for decision in recent_decisions:
                    title = (
                        f"{decision['decision'].upper()} | "
                        f"{decision.get('selected_mode') or decision.get('best_candidate_mode') or 'N/A'} | "
                        f"{decision['created_at']}"
                    )
                    with st.expander(title, expanded=False):
                        st.write(f"Gate ready at decision time: {decision['overall_ready']}")
                        if decision.get("note"):
                            st.write(f"Note: {decision['note']}")
                        if decision.get("deployable_modes"):
                            st.write("Deployable modes: " + ", ".join(decision["deployable_modes"]))
                        blockers = decision.get("blockers") or []
                        if blockers:
                            st.markdown("**Blockers**")
                            for blocker in blockers:
                                st.write(f"- {blocker}")

            with st.expander("Gate thresholds", expanded=False):
                thresholds = gate_result["thresholds"]
                st.write(f"- Minimum offline combined overall: {thresholds['min_combined_overall']:.3f}")
                st.write(f"- Minimum offline standardized RAG overall: {thresholds['min_ragas_overall']:.3f}")
                st.write(f"- Minimum online runs per mode: {int(thresholds['min_online_runs'])}")
                st.write(f"- Minimum online feedback count per mode: {int(thresholds['min_feedback_count'])}")
                st.write(f"- Minimum positive feedback rate: {thresholds['min_positive_rate']:.1%}")
                st.write(f"- Maximum average latency: {thresholds['max_avg_latency_ms']:.0f} ms")

            st.markdown("### Global Online Snapshot")
            st.write(f"Runs observed: {online_summary.get('num_runs', 0)}")
            st.write(f"Feedback captured: {online_summary.get('feedback_count', 0)}")
            st.write(f"Positive feedback rate: {online_summary.get('positive_rate', 0.0):.1%}")
            st.write(f"Average latency: {online_summary.get('avg_latency_ms', 0.0):.1f} ms")
            st.write(f"Cache hit rate: {online_summary.get('cache_hit_rate', 0.0):.1%}")

            st.markdown("### Mode-by-Mode Gate Checks")
            if not gate_result["mode_results"]:
                st.info("Run the offline evaluation to populate deployment-gate checks.")
            else:
                for mode, mode_result in gate_result["mode_results"].items():
                    status_label = "PASS" if mode_result["ready"] else "FAIL"
                    with st.expander(f"{mode.upper()} gate status: {status_label}", expanded=False):
                        offline_summary = mode_result["offline_summary"]
                        online_mode_summary = mode_result["online_summary"]
                        st.write(
                            f"Offline combined overall: "
                            f"{offline_summary.get('average_combined_overall', 0.0):.3f}"
                        )
                        st.write(
                            f"Offline standardized RAG overall: "
                            f"{offline_summary.get('average_ragas_overall', 0.0):.3f}"
                        )
                        st.write(f"Online runs: {online_mode_summary.get('num_runs', 0)}")
                        st.write(
                            f"Online feedback count: {online_mode_summary.get('feedback_count', 0)}"
                        )
                        st.write(
                            f"Online positive rate: {online_mode_summary.get('positive_rate', 0.0):.1%}"
                        )
                        st.write(
                            f"Online average latency: {online_mode_summary.get('avg_latency_ms', 0.0):.1f} ms"
                        )

                        st.markdown("**Checks**")
                        for check in mode_result["checks"]:
                            comparator = check["comparator"]
                            st.write(
                                f"- {'PASS' if check['passed'] else 'FAIL'} | {check['name']}: "
                                f"{check['actual']:.3f} {comparator} {check['threshold']:.3f}"
                            )

with right_col:
    with st.container(border=True):
        render_panel_header(
            "Knowledge Graph",
            caption="Prompt-specific graph previews load in the background so the rest of the app stays responsive.",
        )
        render_graph_panel()

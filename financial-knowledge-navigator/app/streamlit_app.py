import sys
from pathlib import Path

import streamlit as st
from streamlit_agraph import agraph

# Allow imports from project root
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from backend.core.config import settings
from backend.core.cache import ArtifactCache
from backend.core.query_cache import QueryResultCache
from backend.ingestion.loaders import save_uploaded_file, load_pdf_text
from backend.ingestion.chunking import chunk_text
from backend.retrieval.vector_store import VectorStore
from backend.retrieval.bm25_store import BM25Store
from backend.retrieval.hybrid_search import HybridSearcher
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.refined_answer_generator import RefinedAnswerGenerator
from backend.graph.extractor import FinancialGraphExtractor
from backend.graph.builder import FinancialKnowledgeGraph
from backend.graph.visualization import build_agraph_elements, default_graph_config
from backend.graph.query_graph import QueryGraphLinker
from backend.graph.graphrag import GraphRAGEngine
from backend.eval.dataset_loader import load_golden_dataset
from backend.eval.runner import EvaluationRunner
from backend.eval.judge import LLMJudge
from backend.graph.artifact_loader import load_graph_from_extractions
from backend.query_pipeline import QueryPipeline
from backend.eval.reporting import EvaluationReportGenerator
from backend.retrieval.self_corrector import SelfCorrector
from backend.eval.history import EvaluationHistoryManager
from backend.eval.history_reporting import EvaluationHistoryReportGenerator
from backend.core.invalidation import CacheInvalidationManager

st.set_page_config(page_title="Financial Knowledge Navigator", layout="wide")

st.title("Financial Knowledge Navigator")
st.caption("Persistent-cache build: retrieval, graph reasoning, saved artifacts, query caching, and LLM-as-a-judge evaluation")

# -----------------------------
# Session initialization
# -----------------------------
if "artifact_cache" not in st.session_state:
    st.session_state.artifact_cache = ArtifactCache()

if "query_cache" not in st.session_state:
    st.session_state.query_cache = QueryResultCache()

if "vector_store" not in st.session_state:
    st.session_state.vector_store = VectorStore()

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

if "knowledge_graph" not in st.session_state:
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()

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
    )

if "evaluation_runner" not in st.session_state:
    st.session_state.evaluation_runner = EvaluationRunner(
        query_pipeline=st.session_state.query_pipeline,
        llm_judge=st.session_state.llm_judge,
    )

if "report_generator" not in st.session_state:
    st.session_state.report_generator = EvaluationReportGenerator()

if "history_manager" not in st.session_state:
    st.session_state.history_manager = EvaluationHistoryManager()

if "history_report_generator" not in st.session_state:
    st.session_state.history_report_generator = EvaluationHistoryReportGenerator()

if "invalidation_manager" not in st.session_state:
    st.session_state.invalidation_manager = CacheInvalidationManager()

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

if "all_chunks" not in st.session_state:
    st.session_state.all_chunks = []

if "cache_restored" not in st.session_state:
    st.session_state.cache_restored = False

if "last_results" not in st.session_state:
    st.session_state.last_results = []

if "last_vector_results" not in st.session_state:
    st.session_state.last_vector_results = []

if "last_bm25_results" not in st.session_state:
    st.session_state.last_bm25_results = []

if "last_mode" not in st.session_state:
    st.session_state.last_mode = "hybrid"

if "last_query" not in st.session_state:
    st.session_state.last_query = ""

if "last_preliminary_answer" not in st.session_state:
    st.session_state.last_preliminary_answer = ""

if "last_refined_answer" not in st.session_state:
    st.session_state.last_refined_answer = ""

if "last_graph_context_text" not in st.session_state:
    st.session_state.last_graph_context_text = ""

if "last_highlighted_nodes" not in st.session_state:
    st.session_state.last_highlighted_nodes = []

if "last_subgraph" not in st.session_state:
    st.session_state.last_subgraph = None

if "last_eval_results" not in st.session_state:
    st.session_state.last_eval_results = None

if "last_cache_hit" not in st.session_state:
    st.session_state.last_cache_hit = False

if "last_report_paths" not in st.session_state:
    st.session_state.last_report_paths = None

# -----------------------------
# Restore cached artifacts once
# -----------------------------
if not st.session_state.cache_restored:
    cached_docs = st.session_state.artifact_cache.list_indexed_documents()
    extraction_batches = []

    for record in cached_docs:
        st.session_state.indexed_docs.append(record["source_name"])

        cached_chunks = st.session_state.artifact_cache.load_chunks(record["file_hash"])
        if cached_chunks:
            st.session_state.all_chunks.extend(cached_chunks)
            st.session_state.bm25_store.index_chunks(cached_chunks)

        cached_extractions = st.session_state.artifact_cache.load_graph_extractions(record["file_hash"])
        if cached_extractions:
            extraction_batches.append(cached_extractions)

    load_graph_from_extractions(
        st.session_state.knowledge_graph,
        extraction_batches,
    )

    st.session_state.cache_restored = True

if not st.session_state.run_history:
    st.session_state.run_history = st.session_state.history_manager.list_runs()

def rebuild_runtime_state_after_partial_clear():
    """
    Rebuild lightweight runtime-only objects after cache invalidation.
    """
    st.session_state.bm25_store = BM25Store()
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()
    st.session_state.graphrag_engine = GraphRAGEngine(
        knowledge_graph=st.session_state.knowledge_graph,
        query_graph_linker=st.session_state.query_graph_linker,
    )
    st.session_state.query_pipeline = QueryPipeline(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
        hybrid_searcher=st.session_state.hybrid_searcher,
        answer_generator=st.session_state.answer_generator,
        refined_answer_generator=st.session_state.refined_answer_generator,
        graphrag_engine=st.session_state.graphrag_engine,
        query_cache=st.session_state.query_cache,
    )
    st.session_state.evaluation_runner = EvaluationRunner(
        query_pipeline=st.session_state.query_pipeline,
        llm_judge=st.session_state.llm_judge,
    )

def restore_cached_state_into_memory():
    """
    Reload cached chunks and graph extractions from disk into in-memory BM25 and NetworkX.
    """
    st.session_state.indexed_docs = []
    st.session_state.all_chunks = []

    st.session_state.bm25_store = BM25Store()
    st.session_state.knowledge_graph = FinancialKnowledgeGraph()

    cached_docs = st.session_state.artifact_cache.list_indexed_documents()
    extraction_batches = []

    for record in cached_docs:
        st.session_state.indexed_docs.append(record["source_name"])

        cached_chunks = st.session_state.artifact_cache.load_chunks(record["file_hash"])
        if cached_chunks:
            st.session_state.all_chunks.extend(cached_chunks)
            st.session_state.bm25_store.index_chunks(cached_chunks)

        cached_extractions = st.session_state.artifact_cache.load_graph_extractions(record["file_hash"])
        if cached_extractions:
            extraction_batches.append(cached_extractions)

    load_graph_from_extractions(
        st.session_state.knowledge_graph,
        extraction_batches,
    )

    st.session_state.graphrag_engine = GraphRAGEngine(
        knowledge_graph=st.session_state.knowledge_graph,
        query_graph_linker=st.session_state.query_graph_linker,
    )
    st.session_state.query_pipeline = QueryPipeline(
        vector_store=st.session_state.vector_store,
        bm25_store=st.session_state.bm25_store,
        hybrid_searcher=st.session_state.hybrid_searcher,
        answer_generator=st.session_state.answer_generator,
        refined_answer_generator=st.session_state.refined_answer_generator,
        graphrag_engine=st.session_state.graphrag_engine,
        query_cache=st.session_state.query_cache,
    )
    st.session_state.evaluation_runner = EvaluationRunner(
        query_pipeline=st.session_state.query_pipeline,
        llm_judge=st.session_state.llm_judge,
    )

def reset_output_views():
    st.session_state.last_results = []
    st.session_state.last_vector_results = []
    st.session_state.last_bm25_results = []
    st.session_state.last_mode = "hybrid"
    st.session_state.last_query = ""
    st.session_state.last_preliminary_answer = ""
    st.session_state.last_refined_answer = ""
    st.session_state.last_graph_context_text = ""
    st.session_state.last_highlighted_nodes = []
    st.session_state.last_subgraph = None
    st.session_state.last_eval_results = None
    st.session_state.last_report_paths = None
    st.session_state.last_run_comparison = None
    st.session_state.last_comparison_report_path = None
    st.session_state.last_cache_hit = False

left_col, center_col, right_col = st.columns([1, 1.5, 1.6])

with left_col:
    st.subheader("Upload documents")

    uploaded_files = st.file_uploader(
        "Upload one or more PDF files",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if st.button("Process, Index, and Build Graph", type="primary"):
        if not uploaded_files:
            st.warning("Upload at least one PDF first.")
        else:
            total_chunks_added = 0
            total_graph_extractions = 0
            reused_docs = 0

            progress = st.progress(0, text="Starting cache-aware ingestion...")
            files_to_process = uploaded_files

            for idx, uploaded_file in enumerate(files_to_process, start=1):
                progress.progress(
                    min(int((idx - 1) / len(files_to_process) * 100), 100),
                    text=f"Saving {uploaded_file.name}...",
                )

                saved_path = save_uploaded_file(uploaded_file)
                file_hash = st.session_state.artifact_cache.file_sha256(saved_path)
                existing_record = st.session_state.artifact_cache.get_document_record(file_hash)

                if existing_record:
                    if uploaded_file.name not in st.session_state.indexed_docs:
                        st.session_state.indexed_docs.append(uploaded_file.name)

                    cached_chunks = st.session_state.artifact_cache.load_chunks(file_hash)
                    if cached_chunks:
                        known_chunk_ids = {c["chunk_id"] for c in st.session_state.all_chunks}
                        new_local_chunks = [c for c in cached_chunks if c["chunk_id"] not in known_chunk_ids]
                        if new_local_chunks:
                            st.session_state.all_chunks.extend(new_local_chunks)
                            st.session_state.bm25_store.index_chunks(new_local_chunks)

                    reused_docs += 1
                    progress.progress(
                        min(int(idx / len(files_to_process) * 100), 100),
                        text=f"Reused cached artifacts for {uploaded_file.name}",
                    )
                    continue

                raw_text = load_pdf_text(saved_path)
                chunks = chunk_text(
                    text=raw_text,
                    source_name=uploaded_file.name,
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                )

                st.session_state.artifact_cache.save_chunks(file_hash, chunks)

                indexed_count = st.session_state.vector_store.index_chunks(chunks)
                total_chunks_added += indexed_count

                st.session_state.bm25_store.index_chunks(chunks)
                st.session_state.all_chunks.extend(chunks)

                extraction_batch = []
                for chunk in chunks:
                    extracted = st.session_state.graph_extractor.extract_from_chunk(chunk)
                    extraction_batch.append(extracted)
                    total_graph_extractions += 1

                st.session_state.artifact_cache.save_graph_extractions(file_hash, extraction_batch)
                st.session_state.knowledge_graph.build_from_chunks(extraction_batch)

                if uploaded_file.name not in st.session_state.indexed_docs:
                    st.session_state.indexed_docs.append(uploaded_file.name)

                st.session_state.artifact_cache.upsert_document_record(
                    file_hash=file_hash,
                    record={
                        "file_hash": file_hash,
                        "source_name": uploaded_file.name,
                        "saved_path": saved_path,
                        "chunk_count": len(chunks),
                    },
                )

                progress.progress(
                    min(int(idx / len(files_to_process) * 100), 100),
                    text=f"Indexed and cached {uploaded_file.name}",
                )

            progress.progress(100, text="Done.")

            summary = st.session_state.knowledge_graph.graph_summary()
            st.success(
                f"Done. New vector chunks indexed: {total_chunks_added}. "
                f"New graph extractions: {total_graph_extractions}. "
                f"Cached docs reused: {reused_docs}. "
                f"Graph now has {summary['num_nodes']} node(s) and {summary['num_edges']} edge(s)."
            )

    st.markdown("---")
    st.subheader("Indexed documents")

    if st.session_state.indexed_docs:
        for doc in sorted(set(st.session_state.indexed_docs)):
            st.write(f"- {doc}")
    else:
        st.write("No documents indexed yet.")

    st.markdown("---")
    st.subheader("Retrieval mode")

    retrieval_mode = st.radio(
        "Choose retrieval strategy",
        options=["hybrid", "vector", "bm25"],
        horizontal=True,
        index=0,
    )

    use_correction = st.checkbox("Enable Self-Correcting Retrieval (CRAG)", value=False)

    st.markdown("---")
    st.subheader("Graph summary")
    summary = st.session_state.knowledge_graph.graph_summary()
    st.write(f"Nodes: {summary['num_nodes']}")
    st.write(f"Edges: {summary['num_edges']}")

    st.markdown("---")
    st.subheader("Cache summary")
    cached_records = st.session_state.artifact_cache.list_indexed_documents()
    query_cache_stats = st.session_state.query_cache.get_cache_stats()
    st.write(f"Cached documents: {len(cached_records)}")
    st.write(f"Query cache entries: {query_cache_stats['num_entries']}")
    st.write(f"Artifact directory: {settings.artifacts_dir}")
    st.write(f"Qdrant path: {settings.qdrant_path}")

    st.markdown("---")
    st.subheader("Cache / Storage Controls")

    with st.expander("Open cache invalidation controls", expanded=False):
        st.caption("Use these controls carefully. Some actions require rebuilding indices or reprocessing documents.")

        col_a, col_b = st.columns(2)

        with col_a:
            if st.button("Clear Query Cache"):
                result = st.session_state.invalidation_manager.clear_query_cache()
                st.session_state.query_cache = QueryResultCache()
                st.session_state.llm_judge = LLMJudge(query_cache=st.session_state.query_cache)
                rebuild_runtime_state_after_partial_clear()
                reset_output_views()
                st.session_state.last_invalidation_result = ("Cleared query cache", result)
                st.rerun()

            if st.button("Clear Eval Results"):
                result = st.session_state.invalidation_manager.clear_eval_results()
                st.session_state.run_history = st.session_state.history_manager.list_runs()
                st.session_state.last_eval_results = None
                st.session_state.last_invalidation_result = ("Cleared evaluation result files", result)
                st.rerun()

            if st.button("Clear Reports"):
                result = st.session_state.invalidation_manager.clear_reports()
                st.session_state.last_report_paths = None
                st.session_state.last_comparison_report_path = None
                st.session_state.last_invalidation_result = ("Cleared generated reports", result)
                st.rerun()

        with col_b:
            if st.button("Clear Uploaded Files"):
                result = st.session_state.invalidation_manager.clear_uploads()
                st.session_state.last_invalidation_result = ("Cleared uploaded files", result)
                st.rerun()

            if st.button("Clear Artifact Cache"):
                result = st.session_state.invalidation_manager.clear_artifact_cache()

                st.session_state.artifact_cache = ArtifactCache()
                st.session_state.indexed_docs = []
                st.session_state.all_chunks = []

                rebuild_runtime_state_after_partial_clear()
                reset_output_views()

                st.session_state.last_invalidation_result = ("Cleared artifact cache", result)
                st.rerun()

            if st.button("Reset Local Qdrant Index"):
                result = st.session_state.invalidation_manager.clear_qdrant()

                st.session_state.vector_store = VectorStore()
                rebuild_runtime_state_after_partial_clear()
                reset_output_views()

                st.session_state.last_invalidation_result = ("Reset local Qdrant index", result)
                st.rerun()

        st.markdown("---")

        if st.button("Full Local Reset", type="primary"):
            result = st.session_state.invalidation_manager.full_reset()

            st.session_state.artifact_cache = ArtifactCache()
            st.session_state.query_cache = QueryResultCache()
            st.session_state.vector_store = VectorStore()
            st.session_state.bm25_store = BM25Store()
            st.session_state.knowledge_graph = FinancialKnowledgeGraph()
            st.session_state.indexed_docs = []
            st.session_state.all_chunks = []
            st.session_state.run_history = []
            st.session_state.last_eval_results = None
            st.session_state.last_report_paths = None
            st.session_state.last_run_comparison = None
            st.session_state.last_comparison_report_path = None

            st.session_state.graphrag_engine = GraphRAGEngine(
                knowledge_graph=st.session_state.knowledge_graph,
                query_graph_linker=st.session_state.query_graph_linker,
            )
            st.session_state.llm_judge = LLMJudge(query_cache=st.session_state.query_cache)
            st.session_state.query_pipeline = QueryPipeline(
                vector_store=st.session_state.vector_store,
                bm25_store=st.session_state.bm25_store,
                hybrid_searcher=st.session_state.hybrid_searcher,
                answer_generator=st.session_state.answer_generator,
                refined_answer_generator=st.session_state.refined_answer_generator,
                graphrag_engine=st.session_state.graphrag_engine,
                query_cache=st.session_state.query_cache,
            )
            st.session_state.evaluation_runner = EvaluationRunner(
                query_pipeline=st.session_state.query_pipeline,
                llm_judge=st.session_state.llm_judge,
            )

            reset_output_views()
            st.session_state.last_invalidation_result = ("Completed full local reset", result)
            st.rerun()

        if st.button("Reload Cached Artifacts Into Memory"):
            restore_cached_state_into_memory()
            reset_output_views()
            st.session_state.run_history = st.session_state.history_manager.list_runs()
            st.session_state.last_invalidation_result = (
                "Reloaded cached artifacts into runtime memory",
                {"reloaded_documents": len(st.session_state.indexed_docs)},
            )
            st.rerun()

    if st.session_state.last_invalidation_result:
        label, result = st.session_state.last_invalidation_result
        st.markdown("### Last Invalidation Action")
        st.write(label)
        for k, v in result.items():
            st.write(f"- {k}: {v}")

    st.markdown("---")
    st.subheader("Evaluation")

    if st.button("Run Golden Dataset Evaluation"):
        try:
            dataset = load_golden_dataset()
            with st.spinner("Running evaluation across vector, hybrid, and GraphRAG with cached pipeline + LLM judge..."):
                eval_results = st.session_state.evaluation_runner.run_dataset(
                    dataset=dataset,
                    indexed_docs=st.session_state.indexed_docs,
                )
                saved_path = st.session_state.evaluation_runner.save_results(eval_results)
                st.session_state.last_eval_results = eval_results
                st.session_state.run_history = st.session_state.history_manager.list_runs()
                st.session_state.last_report_paths = None

            st.success(f"Evaluation complete. Results saved to: {saved_path}")
        except Exception as e:
            st.error(f"Evaluation failed: {e}")

    if st.button("Export Evaluation Reports"):
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

with center_col:
    st.subheader("Ask a question")

    query = st.text_area(
        "Enter a question about your uploaded financial documents",
        placeholder="Example: How do authorized participants affect ETF liquidity during volatility?",
        height=120,
    )

    if st.button("Run Query"):
        if not query.strip():
            st.warning("Enter a question first.")
        elif not st.session_state.indexed_docs:
            st.warning("Index at least one document first.")
        else:
            pipeline_result = st.session_state.query_pipeline.run(
                query=query,
                mode=retrieval_mode,
                indexed_docs=st.session_state.indexed_docs,
                top_k=settings.top_k,
                use_cache=True,
                use_correction=use_correction,
            )

            st.session_state.last_query = query
            st.session_state.last_mode = retrieval_mode
            st.session_state.last_results = pipeline_result["selected_results"]
            st.session_state.last_vector_results = pipeline_result["vector_results"]
            st.session_state.last_bm25_results = pipeline_result["bm25_results"]
            st.session_state.last_preliminary_answer = pipeline_result["preliminary_answer"]
            st.session_state.last_graph_context_text = pipeline_result["graph_context_text"]
            st.session_state.last_highlighted_nodes = pipeline_result["matched_nodes"]
            st.session_state.last_refined_answer = pipeline_result["refined_answer"]
            st.session_state.last_cache_hit = pipeline_result.get("cache_hit", False)
            st.session_state.last_was_corrected = pipeline_result.get("was_corrected", False)
            st.session_state.last_rewritten_query = pipeline_result.get("rewritten_query", None)

            if pipeline_result["matched_nodes"]:
                st.session_state.last_subgraph = st.session_state.knowledge_graph.subgraph_around_nodes(
                    pipeline_result["matched_nodes"],
                    radius=1,
                )
            else:
                st.session_state.last_subgraph = st.session_state.knowledge_graph.get_graph()

    if st.session_state.last_query:
        if getattr(st.session_state, "last_was_corrected", False):
            st.warning(f"Original query lacked relevant context. Rewritten to: '{st.session_state.last_rewritten_query}'")

        if st.session_state.last_cache_hit:
            st.success("Result loaded from query cache.")
        else:
            st.info("Result generated fresh and saved to query cache.")

    answer_tab, sources_tab, graph_context_tab, eval_tab = st.tabs(
        ["Answer", "Sources", "Graph Context", "Evaluation"]
    )

    with answer_tab:
        if st.session_state.last_preliminary_answer or st.session_state.last_refined_answer:
            st.markdown("### Preliminary Answer")
            st.write(st.session_state.last_preliminary_answer or "No preliminary answer yet.")

            st.markdown("---")
            st.markdown("### Refined Graph-Aware Answer")
            st.write(st.session_state.last_refined_answer or "No refined answer yet.")
        else:
            st.info("Run a query to see answers.")

    with sources_tab:
        st.subheader("Retrieved chunks")

        if st.session_state.last_results:
            for i, result in enumerate(st.session_state.last_results, start=1):
                if st.session_state.last_mode == "hybrid":
                    title = (
                        f"{i}. {result['source']} | "
                        f"rrf={result['rrf_score']:.4f} | "
                        f"vector={result['vector_score'] if result['vector_score'] is not None else '—'} | "
                        f"bm25={result['bm25_score'] if result['bm25_score'] is not None else '—'}"
                    )
                else:
                    title = f"{i}. {result['source']} | score={result['score']:.4f}"

                with st.expander(title, expanded=False):
                    st.caption(result["chunk_id"])
                    st.write(result["text"])
        else:
            st.write("No retrieval results yet.")

    with graph_context_tab:
        if st.session_state.last_graph_context_text:
            st.text(st.session_state.last_graph_context_text)
        else:
            st.info("Run a query to generate graph context.")

    with eval_tab:
        eval_subtab_1, eval_subtab_2, eval_subtab_3 = st.tabs(
            ["Current Results", "Run History", "Run Comparison"]
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
                    st.write(f"Pipeline cache hits: {mode_summary['cache_hits']}")

                    st.markdown("Heuristic metrics:")
                    for metric_name, value in mode_summary["average_heuristic_metrics"].items():
                        st.write(f"- {metric_name}: {value:.3f}")

                    st.markdown("LLM judge metrics:")
                    for metric_name, value in mode_summary["average_llm_metrics"].items():
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
                            st.markdown(f"**{result['question_id']}** — {result['question']}")
                            st.write(f"Combined overall: {result['combined_overall']:.3f}")
                            st.write(f"Heuristic overall: {result['heuristic_overall']:.3f}")
                            st.write(f"LLM overall (0-5): {result['llm_judge']['scores']['overall']}")
                            st.write(f"Pipeline cache hit: {result.get('cache_hit', False)}")

                            st.markdown("LLM Judge Scores:")
                            for k, v in result["llm_judge"]["scores"].items():
                                st.write(f"- {k}: {v}")

                            st.markdown("LLM Judge Summary:")
                            st.write(result["llm_judge"]["summary"])

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
                        f"### Comparison: {st.session_state.last_run_comparison['run_a_name']} → {st.session_state.last_run_comparison['run_b_name']}"
                    )

                    comparison = st.session_state.last_run_comparison["comparison"]

                    for mode, row in comparison.items():
                        with st.expander(f"{mode.upper()} comparison", expanded=True):
                            st.write(f"Combined overall delta: {row['average_combined_overall_delta']:.3f}")
                            st.write(f"Heuristic overall delta: {row['average_heuristic_overall_delta']:.3f}")
                            st.write(f"LLM overall delta: {row['average_llm_overall_delta']:.3f}")
                            st.write(f"Cache hits delta: {row['cache_hits_delta']}")

                            st.markdown("**Heuristic metric deltas**")
                            for key, value in row["heuristic_metric_deltas"].items():
                                st.write(f"- {key}: {value:.3f}")

                            st.markdown("**LLM metric deltas**")
                            for key, value in row["llm_metric_deltas"].items():
                                st.write(f"- {key}: {value:.3f}")

with right_col:
    st.subheader("Knowledge graph")

    full_graph = st.session_state.knowledge_graph.get_graph()

    if full_graph.number_of_nodes() == 0:
        st.info("No graph data yet. Process and index documents first.")
    else:
        graph_to_show = st.session_state.last_subgraph if st.session_state.last_subgraph is not None else full_graph
        highlighted_nodes = st.session_state.last_highlighted_nodes

        if highlighted_nodes:
            st.caption("Showing graph neighborhood linked to the query.")
        else:
            st.caption("Showing full graph.")

        nodes, edges = build_agraph_elements(
            graph_to_show,
            highlighted_nodes=highlighted_nodes,
        )
        config = default_graph_config()

        agraph(nodes=nodes, edges=edges, config=config)

        st.markdown("---")
        st.subheader("Matched graph nodes")
        if highlighted_nodes:
            node_details = st.session_state.knowledge_graph.get_node_details(highlighted_nodes)
            for detail in node_details:
                st.write(f"- {detail['label']} ({detail['entity_type']})")
        else:
            st.write("No query-matched nodes yet.")

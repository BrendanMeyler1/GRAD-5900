# Financial Knowledge Navigator

The **Financial Knowledge Navigator** is an advanced Retrieval-Augmented Generation (RAG) and GraphRAG experimentation platform designed to ingest, retrieve, and reason over complex financial documents (like 10-Ks, earnings transcripts, and analyst reports). It is built with Streamlit and uses powerful LLMs alongside both vector semantic search and topological graph connections.

## Features

- **Hybrid Retrieval System:** Combines semantic Vector search (Qdrant + OpenAI embeddings) with traditional keyword matching (BM25) using Reciprocal Rank Fusion (RRF).
- **GraphRAG:** Employs an LLM to extract financial entities and relationships, constructing a NetworkX-based topological Knowledge Graph. It answers queries with both a baseline RAG generation and a deeper, graph-aware refined generation.
- **Self-Correcting Retrieval (CRAG):** Integrates an active feedback loop where an LLM judges the initial retrieved context. If context is deemed irrelevant, it rewrites your query and searches again to automatically prevent hallucinations.
- **Evaluation Engine (LLM-as-a-Judge):** Comes with a built-in test suite (Golden Dataset) and an LLM-driven Judge to score responses on faithlessness, completeness, and reasoning across all retrieval modes (`vector`, `hybrid`, `graphrag`).
- **Run History & Reporting Dashboards:** Allows you to generate Markdown/CSV evaluation reports and natively compares old vs. new evaluation runs side-by-side highlighting `+` or `-` metric deltas.
- **Aggressive Persistent Caching:** Caches everything from token-chunks, NetworkX extraction graphs, and Qdrant local files to individual query pipeline outputs and LLM-Judge scores in order to drastically speed up repeated experiments. Cache controls in the UI let you cleanly invalidate individual layers.

## Project Structure

```text
financial-knowledge-navigator/
├── app/
│   └── streamlit_app.py                   # Main Streamlit UI
├── backend/
│   ├── core/                              # Global Configs, Invalidation, and Base Cache
│   ├── eval/                              # Evaluator, LLM-Judge, History, and Reporting
│   ├── generation/                        # Base and Graph-aware generation modules
│   ├── graph/                             # Schema, extraction logic, builder, and NetworkX loader
│   ├── ingestion/                         # PyPDF Loaders and overlapping text splitters
│   ├── retrieval/                         # VectorStore, BM25Store, HybridSearch, SelfCorrector
│   └── query_pipeline.py                  # The master execution chain for RAG queries
├── data/
│   ├── artifacts/                         # Highly-cached chunks, raw documents, and local graphs
│   ├── eval_results/                      # JSON snapshots of run evaluations
│   ├── golden_set/                        # golden QA evaluation data
│   ├── qdrant/                            # Persistent local Vector storage
│   ├── reports/                           # Output generated CSVs and Markdown experiment comparisons
│   └── uploads/                           # Temporary user uploaded PDF landing zone
├── tests/                                 # Pytest component-level tests
├── requirements.txt                       # App dependencies
└── .env                                   # API Keys and application globals
```

## How It Works

1. **Ingestion & Indexing:** 
   Uploaded PDFs are chunked and fingerprinted. The pipeline embeddings them into local Qdrant, tokenizes them into an in-memory BM25 index, and issues LLM calls to construct a node/edge knowledge graph. Extracted knowledge is heavily cached so restarting the app instantly restores your prior build.
2. **Query Pipeline:** 
   Upon a query, Hybrid Search invokes a Reciprocal Rank Fusion of vector semantic scores and BM25 heuristic overlaps. 
3. **Graph Traversal:** 
   The application intercepts query entities, maps them against the existing NetworkX structure, and highlights neighborhood subgraphs, merging graph definitions sequentially back into the LLM context limits.
4. **Correction Engine:** 
   If standard retrieval context evaluates poorly, a secondary query rewrite takes place, looping backward to secure missing domain relationships.
5. **Two-Speed Generation:** 
   A 'Preliminary Answer' is streamed instantly using chunks, followed up quickly by a highly refined 'Graph-Aware' secondary answer representing deeper analysis.

## Getting Started

### 1. Requirements

Ensure you have Python 3.10+ installed.

```bash
git clone https://github.com/your-username/financial-knowledge-navigator.git
cd financial-knowledge-navigator
python -m venv venv

# Activate on Windows:
venv\Scripts\activate
# Activate on Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Variables

Create a `.env` file in the root formatting your API keys and configuration boundaries:

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini

QDRANT_COLLECTION=financial_docs
QDRANT_PATH=data/qdrant
ARTIFACTS_DIR=data/artifacts

TOP_K=5
CHUNK_SIZE=700
CHUNK_OVERLAP=120
```

### 3. Run the Application

```bash
streamlit run app/streamlit_app.py
```

### 4. Basic Flow

- Wait for the Streamlit dashboard to launch.
- Navigate to the **"Upload documents"** pane logically pinned to the left column.
- Select large financial PDFs and click **"Process, Index, and Build Graph"**.
- Try standard querying to visualize both your chunks and a graphical topological view in the browser.
- Open the **"Evaluation"** tab to benchmark tests iteratively and track pipeline improvements via generated reports!

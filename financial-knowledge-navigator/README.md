# Financial Knowledge Navigator

Financial Knowledge Navigator is a Streamlit app for grounded question answering over private financial documents.

It is designed around a retrieval-first workflow:
- upload and index documents first
- extract lightweight structured financial facts during ingestion
- ask questions in a persistent chat interface
- optionally build persisted graph data later for deeper GraphRAG queries
- evaluate the system with offline benchmarks, telemetry, and deployment gates

The current default setup is:
- retrieval backend: `openai_file_search`
- graph backend: `sqlite`
- evaluation backend: `auto`

This is the most stable and memory-efficient mode in the repo.

## What The App Does

The app combines private-document retrieval, structured fact extraction, optional graph reasoning, and evaluation in one interface.

Main capabilities:
- persistent chat with saved conversations
- OpenAI hosted file search as the default retrieval path
- optional local retrieval backend with `Qdrant` and `BM25`
- structured fact extraction into a local SQLite store during ingestion
- fact-aware answers and fact-aware reranking
- optional background graph builds per indexed document
- persisted graph storage with `sqlite` by default and optional `neo4j`
- query-time `graphrag` mode
- offline evaluation, online telemetry, thumbs up/down feedback, and deployment gates

## How It Works

### 1. Ingestion

When you upload a document and click `Process Next Document`, the app:
- saves the uploaded file locally
- indexes it for retrieval
- extracts structured financial facts such as revenue, margins, income, cash flow, capex, and similar metrics
- stores metadata and artifacts so the app can restore state later

Graph building does not happen automatically during ingestion.

### 2. Retrieval

The app supports multiple retrieval paths depending on the configured backend.

Hosted retrieval:
- `file_search`
- `graphrag`

Local retrieval:
- `vector`
- `hybrid`
- `bm25`
- `graphrag`

In hosted mode, `file_search` is the recommended default for most use.

### 3. Structured Facts

The structured facts pipeline extracts compact financial metrics from indexed documents and stores them in SQLite.

Those facts are then used for:
- answer grounding
- source references in the UI
- fact-aware reranking
- graph enrichment

### 4. Graph Reasoning

The graph layer is optional.

You can queue background graph builds per indexed document in the `Graph Build` section. Once graph data exists, `graphrag` can expand into persisted graph neighborhoods instead of relying only on temporary query-local graphs.

Supported graph backends:
- `sqlite`
- `neo4j`

### 5. Evaluation

The app supports:
- offline golden dataset evaluation
- custom LLM-as-a-judge scoring
- standardized RAG metrics that prefer native `ragas` when available
- online run logging and thumbs up/down feedback
- deployment gates and release workflow reports

## Architecture At A Glance

Default production-oriented path:
- retrieval: OpenAI hosted file search
- facts store: SQLite
- graph store: SQLite
- optional deeper graph path: Neo4j

Important design choices:
- retrieval-first ingestion to keep memory usage flatter
- one document processed per click in the UI
- background graph jobs instead of graphing during upload
- prompt-level graph preview loading in the background so the rest of the UI stays responsive

## Project Structure

```text
financial-knowledge-navigator/
|-- app/
|   `-- streamlit_app.py
|-- backend/
|   |-- core/
|   |-- eval/
|   |-- generation/
|   |-- graph/
|   |-- ingestion/
|   |-- retrieval/
|   |-- structured/
|   `-- query_pipeline.py
|-- data/
|   |-- artifacts/
|   |-- eval_results/
|   |-- graph/
|   |-- qdrant/
|   |-- reports/
|   |-- structured/
|   `-- uploads/
|-- docs/
|-- scripts/
|-- tests/
`-- requirements.txt
```

## Requirements

Recommended Python:
- `3.10` or `3.11`

The app may work on newer versions, but the safest setup is a clean virtual environment or Conda environment where install and run use the same interpreter.

## Quick Start

### 1. Clone and create an environment

```bash
git clone <your-repo-url>
cd financial-knowledge-navigator
python -m venv venv
```

Activate it:

```bash
# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 2. Install dependencies

Always install with the same interpreter you will use to run Streamlit.

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If you use Anaconda or Conda, be explicit:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app/streamlit_app.py
```

### 3. Create `.env`

Create a `.env` file in the project root before launching the app.

`OPENAI_API_KEY` is required at import time, so the app will not start without it.

Example `.env`:

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini

RETRIEVAL_BACKEND=openai_file_search
GRAPH_BACKEND=sqlite
EVALUATION_BACKEND=auto

QDRANT_COLLECTION=financial_docs
QDRANT_PATH=data/qdrant
ARTIFACTS_DIR=data/artifacts
GRAPH_DB_PATH=data/graph/knowledge_graph.db
FACTS_DB_PATH=data/structured/financial_facts.db
ONLINE_EVAL_DB_PATH=data/telemetry/online_eval.db
RELEASE_WORKFLOW_DB_PATH=data/telemetry/release_workflow.db

NEO4J_URI=
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j

TOP_K=5
CHUNK_SIZE=700
CHUNK_OVERLAP=120

DEPLOY_MIN_COMBINED_OVERALL=0.60
DEPLOY_MIN_RAGAS_OVERALL=0.55
DEPLOY_MIN_ONLINE_RUNS=5
DEPLOY_MIN_FEEDBACK_COUNT=2
DEPLOY_MIN_POSITIVE_RATE=0.60
DEPLOY_MAX_AVG_LATENCY_MS=5000
```

### 4. Run the app

```bash
python -m streamlit run app/streamlit_app.py
```

On Windows with Anaconda:

```bash
C:\Users\your-user\anaconda3\python.exe -m pip install -r requirements.txt
C:\Users\your-user\anaconda3\python.exe -m streamlit run app/streamlit_app.py
```

## Recommended Setup Modes

### Recommended default mode

Use this for the cleanest first run:

```env
RETRIEVAL_BACKEND=openai_file_search
GRAPH_BACKEND=sqlite
EVALUATION_BACKEND=auto
```

### Local retrieval experiment

Use this when you want to compare against the local vector/BM25 stack:

```env
RETRIEVAL_BACKEND=local_vector
GRAPH_BACKEND=sqlite
```

### Neo4j graph backend

Use this when you want deeper persisted graph behavior:

```env
RETRIEVAL_BACKEND=openai_file_search
GRAPH_BACKEND=neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

## Local Neo4j Setup

If you want to run Neo4j locally, helper scripts are included:
- [setup_neo4j_local.ps1](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/scripts/setup_neo4j_local.ps1)
- [start_neo4j_local.ps1](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/scripts/start_neo4j_local.ps1)
- [stop_neo4j_local.ps1](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/scripts/stop_neo4j_local.ps1)

Typical local flow:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j_local.ps1
python -m streamlit run app/streamlit_app.py
```

Then set:
- `GRAPH_BACKEND=neo4j`
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

## First Run Workflow

Once the app is open:

1. Start in the default backend configuration.
2. Upload one or more `PDF`, `TXT`, or `HTML` files.
3. Click `Process Next Document` once per file.
4. Watch the `Data Status` section to confirm indexed documents and structured facts are increasing.
5. Ask questions in the center chat.
6. Review:
   - `Answer`
   - `Sources`
   - `Structured Facts`
   - `Graph Context`
   - `Evaluation`
7. If you want persisted graph reasoning, open `Graph Build` and queue background graph jobs for selected documents.
8. Use the right-side graph panel to inspect prompt-specific graph neighborhoods.

## Main UI Areas

### Workflow Summary

Shows:
- indexed document count
- graph-ready document count
- structured fact count
- active backend choices
- the next recommended step

### Conversations

Supports:
- new chats
- renaming the current chat
- revisiting saved conversations

### Documents

Supports:
- file upload
- one-document-per-click processing
- indexed document review

### Query Settings

Lets you choose:
- retrieval mode
- optional self-correcting retrieval

### Graph Build

Lets you:
- queue background graph builds per indexed document
- inspect recent graph jobs

### Data Status

Shows:
- graph node and edge counts
- structured fact counts
- query cache counts
- backend details and storage paths

### Advanced Maintenance

Includes:
- query cache reset
- uploaded file reset
- artifact reset
- retrieval store reset
- graph reset
- structured facts reset
- online telemetry reset
- release workflow reset
- full app reset

### Evaluation Tools

Supports:
- golden dataset evaluation
- report export
- run history
- run comparison
- online telemetry review
- deployment gates
- release workflow decisions

## Retrieval Modes

### Hosted retrieval

When `RETRIEVAL_BACKEND=openai_file_search`, the UI exposes:
- `file_search`
- `graphrag`

Behavior:
- `file_search` uses hosted OpenAI retrieval
- `graphrag` uses hosted retrieval first, then graph expansion

### Local retrieval

When `RETRIEVAL_BACKEND=local_vector`, the UI exposes:
- `vector`
- `hybrid`
- `bm25`
- `graphrag`

Behavior:
- `vector` uses local Qdrant
- `hybrid` fuses vector search with BM25
- `bm25` uses keyword retrieval only
- `graphrag` uses local retrieval before graph expansion

## Graph Behavior

Important graph rules:
- uploading does not automatically build persisted graph data
- graph builds happen later and on demand
- the right-side graph preview loads in the background
- `graphrag` prefers persisted graph neighborhoods when available
- if no persisted graph data exists, the app can still fall back to lighter prompt-level graph behavior

Graph backend options:
- `sqlite` works out of the box
- `neo4j` gives you the deeper persisted graph path when configured

## Evaluation And Telemetry

The app includes:
- offline golden dataset evaluation
- heuristic retrieval metrics
- LLM-as-a-judge scoring
- standardized RAG metrics
- thumbs up/down answer feedback
- online telemetry summaries
- deployment gates
- persisted release workflow decisions

If native `ragas` is available in the active interpreter, the app prefers it. Otherwise it falls back to the local proxy scorer.

## Cache And Reset Behavior

The app can clear:
- query cache
- uploaded files
- artifact cache
- structured facts
- persisted graph store
- online telemetry
- release workflow history
- retrieval store state
- full local app state

Notes:
- `Reset Hosted Retrieval Store` recreates the hosted OpenAI vector store used by this app
- `Reset Local Retrieval Index` rebuilds the local Qdrant-backed path
- `Full App Reset` clears saved conversations, artifacts, graph state, facts, telemetry, and the active retrieval backend state

## Troubleshooting

### `ModuleNotFoundError: No module named 'streamlit_agraph'`

Install dependencies with the same Python interpreter used to launch Streamlit:

```bash
python -m pip install -r requirements.txt
```

### `OPENAI_API_KEY is missing`

Add it to `.env` before launching the app.

### `QdrantLocal instance is closed`

Restart the app. The local retriever recreates its client automatically when needed.

### Neo4j selected but graph features are disabled

Set:
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- optionally `NEO4J_DATABASE`

Also make sure the Neo4j server is actually running.

### The app starts but retrieval modes are missing

Check `RETRIEVAL_BACKEND` in `.env`.

Expected mode sets:
- hosted mode: `file_search`, `graphrag`
- local mode: `vector`, `hybrid`, `bm25`, `graphrag`

### The app loads slowly on first interaction

Hosted retrieval initializes lazily now, but the first upload or hosted query can still take longer than later interactions.

### Graph preview says no connections

That can happen when:
- no persisted graph exists yet for the relevant documents
- the prompt did not produce a usable graph neighborhood
- you are in `file_search` mode and only a lightweight preview was available

Queue background graph builds for the documents you care about if you want deeper persisted graph context.

## Testing

Run the full test suite with:

```bash
python -m pytest -q tests
```

Targeted backend and graph tests include:
- [test_graph_factory.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/tests/test_graph_factory.py)
- [test_retrieval_factory.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/tests/test_retrieval_factory.py)
- [test_background_jobs.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/tests/test_background_jobs.py)
- [test_neo4j_store.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/tests/test_neo4j_store.py)

## Project Status

Current status by phase:
- Phase 1: complete
- Phase 2: complete for the current retrieval, fact, and graph scope
- Phase 3: in place for evaluation, telemetry, deployment gates, and release workflow

If you want the implementation checklist and scaffolding history, see [phase1-implementation-checklist.md](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/docs/phase1-implementation-checklist.md).

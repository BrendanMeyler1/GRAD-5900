# Phase 1 Implementation Checklist

Goal: introduce the extension points and backend scaffolding needed for a retrieval-first, graph-later architecture without destabilizing the current app.

## Current Status

Phase 1 is complete.

Phase 2 has now started with structured financial fact extraction and a persisted facts store, but the checklist below remains the record of the completed Phase 1 work.

In addition to the original Phase 1 scope, the repo now also has:

- retrieval and graph factory layers wired into the app
- backend-selectable retrieval modes in the UI
- backend-agnostic background graph jobs through the shared `Retriever` and `GraphStore` contracts
- a retrieval-first upload flow where graph builds happen later and on demand

## Scope

- Add explicit retrieval, graph, and evaluation interfaces.
- Keep OpenAI hosted file search as the default retrieval path.
- Keep SQLite as the local graph fallback.
- Scaffold a Neo4j-backed graph store for future GraphRAG work.
- Add config needed to choose graph backends and connect to Neo4j.
- Add tests for the new interfaces and Neo4j scaffold.

## Deliverables

- [x] Add `Retriever` base interface in [backend/retrieval/base.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/retrieval/base.py)
- [x] Add `GraphStore` base interface in [backend/graph/base.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/graph/base.py)
- [x] Add evaluation interfaces in [backend/eval/base.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/eval/base.py)
- [x] Update [backend/retrieval/openai_file_search_store.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/retrieval/openai_file_search_store.py) to implement `Retriever`
- [x] Update [backend/graph/sqlite_store.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/graph/sqlite_store.py) to implement `GraphStore`
- [x] Update [backend/eval/judge.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/eval/judge.py) and [backend/eval/runner.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/eval/runner.py) to implement evaluation interfaces
- [x] Add Neo4j config fields in [backend/core/config.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/core/config.py)
- [x] Add `Neo4jGraphStore` scaffold in [backend/graph/neo4j_store.py](c:/Users/mebre/OneDrive/Desktop/Masters/GRAD%205900/financial-knowledge-navigator/backend/graph/neo4j_store.py)
- [x] Add targeted tests for the Neo4j scaffold
- [x] Keep the current Streamlit app working without requiring Neo4j

## Acceptance Criteria

- [x] Imports succeed when Neo4j is not installed or not configured
- [x] Existing default app behavior remains usable without Neo4j
- [x] Neo4j scaffold can be instantiated with an injected driver in tests
- [x] Neo4j scaffold exposes the same document-graph methods as SQLite
- [x] Code compiles and tests pass

## Out of Scope

- Switching the running app to Neo4j by default
- Migrating background graph jobs from SQLite to Neo4j
- Adding Ragas integration
- Adding online evaluation telemetry

## Follow-on Phases

### Phase 2

- Expand the newly added structured fact extraction and storage into richer fact-grounded retrieval and answer support
- Deepen Neo4j-backed graph persistence beyond the current scaffold
- Add richer graph job orchestration and status handling
- Keep tightening backend-specific UI and evaluation behavior

### Phase 3

- Add query-time Neo4j neighborhood expansion for GraphRAG
- Add Ragas-backed offline evaluation
- Add online run logging and deployment gates

Progress update:

- query-time persisted graph neighborhood expansion is now in place
- the app now includes a standardized RAG metrics layer in offline evaluation runs that prefers native `ragas` when available and falls back to a local proxy backend otherwise
- persistent online run logging with thumbs-up / thumbs-down feedback and mode-level telemetry summaries is now in place
- deployment gates are now in place on top of the offline evaluation and online telemetry layers
- persisted release workflow decisions and release report export are now in place on top of the gate signals

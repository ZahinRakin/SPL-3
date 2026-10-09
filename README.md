# GraphRAG Intelligence

An intelligence tool with workspaces, cases, evidence uploads, a knowledge graph,
and document Q&A. The backend combines RAPTOR, GraphRAG, and HippoRAG in a cascade.
Queries use either `standard` (plain RAG) or `refined` (the cascade) mode.

Generation uses an OpenAI-compatible API (OpenRouter by default); embeddings use
Ollama locally. The frontend is Angular 17, and the backend is FastAPI with
PostgreSQL and pgvector.

## Project layout

```text
graphrag-project/
|-- frontend/               Angular app, npm dependencies, and build configuration
|   |-- src/app/            Pages, components, auth, services, and shared helpers
|-- backend/
|   |-- app/                FastAPI entry point and dependencies
|   |-- api/                HTTP routes
|   |-- core/               Settings, database sessions, and logging
|   |-- models/             Database models
|   |-- schemas/            Request and response schemas
|   |-- services/           Auth, access, storage, and index persistence
|   |-- pipeline/           Document processing and retrieval algorithms
|   |-- migrations/         Alembic environment and migration revisions
|   |-- tests/              Offline Python tests
|   |-- evaluation/         Evaluation tools, benchmarks, results, and experiments
|   |-- docs/               Backend extension plan
|   |-- .env / .env.example Local settings / settings template
|   |-- pytest.ini          Test discovery and import paths
|   |-- .graph_rag/         Backend-local Python environment (ignored by Git)
|   |-- alembic.ini         Migration configuration
|   |-- requirements.txt    Python dependencies
|-- data/                   Sample inputs and existing uploaded evidence
|-- .graph_rag/             Verified Python 3.11 environment (ignored by Git)
|-- ARCHITECTURE.md         Project architecture, contracts, and decisions
|-- CLAUDE.md               Repository working instructions
```

Run the server, migrations, and tests from `backend/` using the commands below.
The frontend runs from `frontend/`; evaluation commands below run from the project root.
Settings always load `backend/.env`, regardless of the working directory. Relative
upload paths remain relative to the project root, preserving existing `data/uploads/`
files and paths stored in the database.

## Setup and running

Use Python 3.11, Node.js/npm, PostgreSQL with pgvector, and Ollama.
Copy `backend/.env.example` to `backend/.env` and configure `LLM_API_KEY`, `DATABASE_URL`, and
`JWT_SECRET_KEY`. Optional Google OAuth settings are documented in the template.
Never commit `.env`.

```powershell
# From graphrag-project/, enter the backend and activate the existing Python 3.11 environment.
cd backend
..\.graph_rag\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ollama pull nomic-embed-text
# Keep Ollama running; enable the vector extension in your PostgreSQL database.
python -m alembic -c alembic.ini upgrade head
python -m uvicorn backend.app.main:app --app-dir .. --reload --port 8000
```

`--app-dir ..` adds the project root to Python's import path, so the existing
`backend.*` imports work when launched from `backend/`.
The commands use the original root `.graph_rag/` environment. The separate
`backend/.graph_rag/` currently uses Python 3.14, and Windows Application Control
blocks some of its compiled dependencies; use the verified root environment.
From the project root, the server command is
`python -m uvicorn backend.app.main:app --reload --port 8000`.

API documentation: http://localhost:8000/docs.

In another terminal:

```powershell
cd frontend
npm install
npm start
```

Frontend: http://localhost:4200. The current API service connects to
`http://localhost:8000/api` using the backend's CORS settings.

## Verification

```powershell
# From backend/, with your environment active; LLM and embeddings are stubbed.
python -m pytest -q

# Alternatively, from the project root, explicitly select the backend config.
python -m pytest -c backend/pytest.ini -q

# From frontend/; validates TypeScript and Angular templates.
npm run build -- --configuration development
```

## Evaluation

```powershell
# From graphrag-project/, with your Python environment active.
# QA format: [{"question": "...", "reference": "..."}]
python -m backend.evaluation.eval --qa_pairs eval_data.json --docs_dir data/input/sample_cases --method refined --output eval_report.json
python -m backend.evaluation.compare analyze --exp backend/evaluation/experiments/2026-10-08_standard_vs_refined
```

The benchmark harness is available as `python -m backend.evaluation.harness.<tool>`.
See [the evaluation report](backend/evaluation/REPORT.md) and
[judging handoff](backend/evaluation/JUDGING_HANDOFF.md) for its results and workflow.
Saved benchmark and experiment artifacts retain their original contents, including
historical commands and paths recorded before the directory move.

See [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries, interfaces, and known
limitations, and [the extension plan](backend/docs/EXTENSION_PLAN.md) for the design history.

## Research papers

- [RAPTOR](https://arxiv.org/abs/2401.18059)
- [GraphRAG](https://arxiv.org/abs/2404.16130)
- [HippoRAG](https://arxiv.org/abs/2405.14831)

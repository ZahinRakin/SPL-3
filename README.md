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
|   |-- .env / .env.example Local settings / settings template
|   |-- pytest.ini          Test discovery and import paths
|   |-- .graph_rag/         Python 3.11 virtual environment (ignored by Git)
|   |-- alembic.ini         Migration configuration
|   |-- requirements.txt    Python dependencies
|-- data/                   Sample inputs and existing uploaded evidence
|-- ARCHITECTURE.md         Project architecture, contracts, and decisions
|-- CLAUDE.md               Repository working instructions
```

## Setup and running

Use Python 3.11, Node.js/npm, PostgreSQL with pgvector, and Ollama.
Copy `backend/.env.example` to `backend/.env` and configure `LLM_API_KEY`, `DATABASE_URL`, and
`JWT_SECRET_KEY`. Optional Google OAuth settings are documented in the template.
Never commit `.env`.

```powershell
# From graphrag-project/, enter the backend.
cd backend
# Only once: create the Python 3.11 environment and install dependencies.
py -3.11 -m venv .graph_rag
.\.graph_rag\Scripts\Activate.ps1
pip install -r requirements.txt
ollama pull nomic-embed-text
# Keep Ollama running; enable the vector extension in your PostgreSQL database.
alembic -c alembic.ini upgrade head

# Every time:
.\.graph_rag\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

API documentation: http://localhost:8000/docs.

If startup fails with `DLL load failed ... An Application Control policy has blocked
this file`, Windows Smart App Control is blocking a compiled package (such as asyncpg).
Turn it off in Windows Security → App & browser control, then try again.

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

# From frontend/; validates TypeScript and Angular templates.
npm run build -- --configuration development
```

## Evaluation

```powershell
# From graphrag-project/, with backend\.graph_rag active.
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

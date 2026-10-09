# CLAUDE.md — agent instructions for graphrag-project

Read `ARCHITECTURE.md` before any non-trivial change. It defines the goal, non-goals,
module boundaries, API contract and decisions already made. This file covers **how to work**
in this repo.

This is an academic SPL-3 demo project, not production. The priorities are code that is
correct, readable and complete enough to demo and explain in a viva. Don't make it clever.

---

## Hard rules (ask first, every time)

1. **Never change a public interface without asking.** Public interfaces are:
   - any HTTP path, method, request or response field, or status code under `/api`
     (`backend/api/*.py`, `backend/schemas/*.py`)
   - the TypeScript interfaces in `frontend/src/app/services/api.service.ts`
   - the public method signatures listed in ARCHITECTURE.md §6.3
     (`generate`, `embed`, `extract_text`, `chunk_text`, `index_document`, `build_tree`,
     `index_passages`, `retrieve*`, `query`, `get_*`)
   - the eval CLI flags and the `qa_pairs` JSON format
   - the database schema: change it only through a **new** Alembic migration in
     `backend/migrations/versions/` (never edit an applied one), and update ARCHITECTURE.md §6.3

   If a change truly needs one of these, stop and propose it with the reason. If it's
   approved, update the backend schema, `api.service.ts` and ARCHITECTURE.md §6 **together**.
2. **No new dependencies without approval.** This covers pip, npm, and Ollama/LLM model or
   provider changes. Say what you'd add and why, and what the no-dependency alternative is.
   `backend/requirements.txt` and `frontend/package.json` must not change silently.
3. **Don't move off the decided stack** (ARCHITECTURE.md §7 and §9). In particular, all
   LLM and embedding calls go through `backend/pipeline/llm_provider.py`. Nothing else
   imports `openai` or calls Ollama.
4. **Never read, print, log or commit `.env`**, or put API keys anywhere. Use `backend/.env.example`
   for documenting config. Never log passwords, access/refresh tokens, OAuth codes or the
   database URL.
5. **Don't touch** `../assets/`, `../diagrams/`, `../mid_presentation/`, the proposal `.md`
   files, `data/input/sample_cases/`, or `frontend/package-lock.json`. Edit `backend/.graph_rag/`
   (the venv) only when asked.
6. **Don't fix the known limitations in ARCHITECTURE.md §8 as a side effect.** Mention them
   if they're relevant; fix them only as their own task.
7. Never use a local model larger than about 4B parameters. The dev machine has a 4 GB
   VRAM GPU and about 8 GB of RAM.

## Running things

Run everything from `graphrag-project/` unless stated otherwise. The shell is Windows (PowerShell).

Keep backend migrations, tests, evaluation, docs, requirements, and migration config
under `backend/`. `backend/pytest.ini` discovers `backend/tests/` and adds the project
root to the import path. Environment files live in `backend/`; settings load `backend/.env`
independently of the working directory. The owner approved
keeping `data/` at the root to preserve upload paths. The only venv is
`backend/.graph_rag/` (Python 3.11); activate it for every Python command below, whichever
directory you run from. Keep project-wide documentation and Git
settings at the root; ask before adding another root directory.

```powershell
# one-time
.\backend\.graph_rag\Scripts\Activate.ps1  # Python 3.11 venv
pip install -r backend/requirements.txt
ollama pull nomic-embed-text
# PostgreSQL 18 with pgvector; once per database, as postgres:  CREATE EXTENSION vector;
alembic -c backend/alembic.ini upgrade head                        # create/upgrade the schema (DATABASE_URL from .env)

# after changing backend/models/*.py
alembic -c backend/alembic.ini revision --autogenerate -m "what changed"   # then READ the file and fix it by hand
alembic -c backend/alembic.ini upgrade head

# backend  (needs Postgres, `ollama serve`, and LLM_API_KEY, DATABASE_URL, JWT_SECRET_KEY in .env)
# The owner starts it from backend/; app/main.py puts the project root on sys.path for this.
cd backend; uvicorn app.main:app --reload --port 8000  # docs: http://localhost:8000/docs
# From the root, this also works: uvicorn backend.app.main:app --reload --port 8000
curl http://localhost:8000/api/health

# frontend
cd frontend; npm install; npm start                    # http://localhost:4200 (uses proxy.conf.json)

# evaluation
python -m backend.evaluation.eval --qa_pairs eval_data.json --docs_dir data/input/sample_cases --method refined --output eval_report.json
python -m backend.evaluation.compare analyze --exp backend/evaluation/experiments/2026-10-08_standard_vs_refined   # standard vs refined study
```

## Tests and verification

`python -m pytest -c backend/pytest.ini -q` from the root, or `python -m pytest -q` from
`backend/`, runs the offline tests in `backend/tests/` (LLM and embeddings stubbed,
no Ollama, API key or database needed; ~2 s). Run it after any pipeline change and add a test for
new pipeline behaviour. The API, auth and database have no automated tests, so also verify every
change with the cheapest check that covers it, and say in your summary which checks you ran.

| Changed | Minimum check |
|---|---|
| Any backend Python | `python -c "import backend.app.main"` (catches import and syntax errors) |
| API / schemas | Start uvicorn and hit the changed endpoint with `curl.exe` (log in first: `POST /api/auth/jwt/login`), or use `/docs` → Authorize |
| Models / migrations | `alembic -c backend/alembic.ini upgrade head`, `alembic -c backend/alembic.ini check` (no drift), and `alembic -c backend/alembic.ini downgrade -1` + `upgrade head` on a throwaway DB |
| Access control | Check the route with a non-member (404), a too-low role (403) and an allowed role |
| Pipeline logic | `python -m pytest -c backend/pytest.ini -q` from the root, then in a case, upload `data/input/sample_cases/dhaka.txt` (small), wait for `indexed`, run one query per method; restart uvicorn and confirm the graph/stats are unchanged |
| Frontend | `cd frontend; npx ng build --configuration development` (strict TS + strict templates) |
| Eval | Run the eval on 1–2 QA pairs |

If a check can't run (Ollama or the LLM API unreachable, no key or credits), say so plainly. Don't claim it passed.

## Coding style

### Python (backend, evaluation)
- Python 3.11. Use type hints on every function signature, in the existing `typing` style
  (`List`, `Dict`, `Optional`). Stay consistent within a file.
- Internal data uses `@dataclass` (see `Entity`, `Relationship`, `RaptorNode`). Anything that
  crosses HTTP is a Pydantic model in `backend/schemas/`.
- Imports: absolute `backend.…` in `api/`, `app/`, `core/`, `models/`, `services/` and
  `backend/evaluation/`; relative `.module` inside `pipeline/`.
- **Database:** routes take `db: AsyncSession = Depends(get_db)` and call `await db.commit()`
  themselves; background tasks open `session_scope()`. Every case-scoped query filters by
  `case_id`. Protected routes declare access with `Depends(require_case_role(...))` or
  `Depends(require_workspace_role(...))`. Write an audit event (`services/audit.py`) for every
  mutation. `pipeline/` never imports `models`, `services` or `core.database`.
- **Async:** route handlers and pipeline I/O are `async`. Wrap blocking calls (SDKs, file
  parsing, NetworkX or sklearn on large inputs) in `asyncio.to_thread`. Fan out with
  `asyncio.gather(..., return_exceptions=True)` and handle each exception result.
- **Errors:** follow decision D6, "degrade, don't crash". Catch the error, `logger.warning` or
  `logger.error(..., exc_info=True)`, then return a sensible fallback. Only let it propagate
  when the caller must mark the document as `error`. Never use a bare `except:`.
- **Logging:** use `from backend.core.logger import logger`. Never use `print`. Use f-strings and
  `!r` for user strings. `info` is for lifecycle milestones, `debug` for sizes and counts,
  `warning` for fallbacks.
- Group code into sections with the existing divider style: `# ── section ───…`.
- Prompts are module-level `_UPPER_SNAKE` constants. Escape literal braces as `{{ }}` for
  `.format`.
- Name tunable numbers (chunk size, top-k, penalties, timeouts) as constructor args or module
  constants. Don't bury magic numbers inline.
- Keep anything stochastic seeded (`seed=42` / `random_state=42`).

### TypeScript / Angular (frontend)
- Angular 17 **standalone** components with inline `template` and `styles`, matching the
  existing files. Don't add NgModules.
- Prefer **signals** for component state. Use the new control flow (`@if`, `@for`), not `*ngIf` / `*ngFor`.
- All HTTP goes through `ApiService`. Components never inject `HttpClient`.
- `tsconfig` is strict with `strictTemplates`. Don't use `any` unless D3 typing forces it,
  and then add a comment saying why.
- Colours come from the CSS custom properties in `src/styles.scss` (`var(--accent)`,
  `var(--c-person)`, …). Don't hard-code hex values in components.
- Clean up subscriptions and intervals in `ngOnDestroy`.

### General
- Make the smallest change that solves the task. Don't reformat or rename unrelated code.
- Match the surrounding comment density. Comments explain *why*, not *what*.
- If a change affects behaviour described in ARCHITECTURE.md, update that document in the same change.

## Domain notes (things that are easy to get wrong)

- The proposals, README and the owner may say "**Gemini**". The code actually uses **OpenRouter**
  (`openai/gpt-oss-20b`, through the `openai` package) for generation and **Ollama** (`nomic-embed-text`) for
  embeddings. Don't "fix" the code back to Gemini.
- The pipeline is a **cascade** (RAPTOR → GraphRAG → HippoRAG), answered in two modes:
  `standard` (plain RAG baseline) and `refined` (the cascade). "**HippoRAG**" here *is* the paper's
  Personalized PageRank (decision D7, since 2026-10-08); the old mean-pooling "HiPPO" is gone.
  Don't reintroduce per-method modes (`graphrag`/`raptor`/`hippo`/`hybrid`).
- The `api_key` constructor parameters on the pipeline classes are unused legacy
  parameters. Leave them alone (they are part of the interface).
- Uploads live in `data/uploads/` (`UPLOAD_DIR` in `.env`). Files there are test debris, not
  fixtures. Use `data/input/sample_cases/` for sample inputs.

## Workflow

- For anything larger than a small fix, outline the plan (files touched, interfaces
  affected) before editing.
- When done, summarise: what changed, which checks you ran and their results, and anything
  left open.
- The project isn't a git repo yet. Don't run `git init`, commit or push unless asked.

---

## Corrections log

Every time the owner corrects the agent on something it might repeat, add one line here:
the date, the rule, and the reason in a few words. Newest first. This list overrides the
defaults above if they conflict.

- 2026-10-09 — The owner runs the server from inside `backend/` with `uvicorn app.main:app`. Keep the `sys.path` shim at the top of `backend/app/main.py` and keep imports rooted at `backend.*`. The venv is `backend/.graph_rag/` (Python 3.11; the root venv was removed). Windows Smart App Control blocked compiled wheels (asyncpg, sklearn) and has been turned off; if a "DLL load failed … Application Control policy" error appears, it's that, not the code.
- 2026-10-09 — Environment files and pytest config live in `backend/`. Preserve project-root-relative evidence paths.
- 2026-10-06 — Generation moved from Groq to OpenRouter (`openai` package, `LLM_*` settings): Groq's paid tier was unavailable. Don't reintroduce the `groq` SDK.
- 2026-10-04 — (seed) Generation uses Groq and embeddings use Ollama, even when someone says "Gemini". The code moved off Gemini.

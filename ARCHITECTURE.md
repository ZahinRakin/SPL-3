# GraphRAG Investigations — Architecture & Spec

> Status: **DRAFT — pending owner review.** Updated 2026-10-04 for the investigation-tool
> extension (auth, cases, PostgreSQL). The original plan is in `docs/EXTENSION_PLAN.md`.
> Sections marked **[OPEN]** are things the owner still needs to decide.
> When code and this document disagree, fix one of them; don't leave the gap.

---

## 1. Goal

A full-stack **investigation tool** for the SPL-3 course project. People and organizations
register, open **cases**, and upload evidence files (PDF / DOCX / TXT / HTML) to each case.
Every case gets its own indexes, built three ways, and its own Q&A chat over its evidence.

The project has to show:

1. **Three retrieval architectures, implemented side by side**, based on the papers in
   `../assets/papers/`:
   - **GraphRAG**: an LLM-extracted knowledge graph, Louvain communities and community summaries.
   - **RAPTOR**: a recursive summary tree built with GMM clustering.
   - **HiPPO**: a pyramid of mean-pooled passage embeddings (see §9 for how it differs from the paper).
2. **A hybrid mode** that merges the context from all three before generating an answer.
3. **A real-life use**: multi-user, access-controlled investigation cases stored in PostgreSQL.
4. **A side-by-side evaluation** of the four modes (`graphrag`, `raptor`, `hippo`, `hybrid`)
   using ROUGE, BLEU-1, entity recall, faithfulness and latency. A later phase adds a
   traditional ("naive") RAG baseline (`docs/EXTENSION_PLAN.md` §9). Every chat turn already
   stores its method and latency to support that comparison.

## 2. Non-goals

These are deliberately out of scope. Don't build them unless the owner asks.

- **Production hardening**: no rate limiting (including on login), no horizontal scaling, no HTTPS setup.
- **Email flows**: no email verification, password reset or email invitations (there's no
  mail server). Members are added by the email of an already-registered account.
- **Removing a document's knowledge from the indexes.** Delete removes the document row and
  file only (see §8).
- **OCR for scanned PDFs, or image and table understanding.** Only text extraction is supported.
- **Streaming answers.** Each query returns a single JSON response.
- **Fine-tuning or training models.**
- **Running large local models.** The dev machine has a 4 GB VRAM GPU and about 8 GB of RAM.
  Local models must be about 4B parameters or smaller. Generation runs on Groq; only
  embeddings run locally.

## 3. System overview

```
 Angular 17 SPA (localhost:4200)
   /login /register /auth/google/callback /cases /cases/:caseId /workspaces/:id/members
   AuthService (signals, token in memory) ─ authInterceptor (Bearer, refresh once on 401) ─ ApiService
                                   │ HTTP JSON /api/*   (+ httpOnly refresh cookie on /api/auth)
 FastAPI (localhost:8000)
   api/auth.py (fastapi-users) · workspaces.py · cases.py · documents.py · graph.py · query.py
        │ Depends(current_active_user) → Depends(require_workspace_role / require_case_role)
   services/  users · refresh_tokens · access · audit · storage · case_index_registry · index_store
   models/    SQLAlchemy ORM           core/database.py  async engine + sessions
   pipeline/  GraphRAGIndexer · RaptorRunner · HippoRetriever · QueryEngine  (one bundle per case)
              llm_provider.generate / embed
        │                         │                          │
  PostgreSQL 18 + pgvector   Groq Cloud API             Ollama (localhost:11434)
  (database spl3)            (GROQ_MODEL)               nomic-embed-text (768-d)
```

## 4. Module boundaries

All paths are relative to `graphrag-project/`.

### Backend (`backend/`)

| Module | Responsibility | May depend on | Must NOT |
|---|---|---|---|
| `app/main.py` | Creates the FastAPI app, CORS, `/api/health`, lifespan (JWT-secret check, DB ping, marks interrupted indexing as `error`) | `core`, `api`, `models`, `pipeline.llm_provider` | contain business logic |
| `app/dependencies.py` | `require_workspace_role(min)` / `require_case_role(min)` access dependencies; `get_case_index_registry()` | `services`, `models` | be bypassed. Every protected route declares its minimum role here |
| `api/*.py` | HTTP layer: validation, status codes, commits, background tasks | `schemas`, `core`, `models`, `services`, `app.dependencies`, `pipeline.document_processor` | call Groq or Ollama directly, or hold algorithm logic |
| `schemas/*.py` | Pydantic request and response models (the **public API contract**) | `pydantic`, `fastapi_users.schemas` | import pipeline code |
| `models/*.py` | SQLAlchemy ORM tables (§6.3) | `core.database` (Base) | import `pipeline` or `schemas` |
| `core/config.py` | `Settings` loaded from `.env` (pydantic-settings) | — | — |
| `core/database.py` | Async engine, `SessionLocal`, `get_db()` (per request), `session_scope()` (background tasks), `Base` with a naming convention | `core.config` | contain queries |
| `core/logger.py` | Shared `logger` | — | — |
| `services/users.py` | fastapi-users wiring: `UserManager` (password rules, personal workspace on sign-up), JWT strategy, `RefreshCookieBackend`, Google OAuth client | `models`, `services.refresh_tokens`, `services.audit` | — |
| `services/refresh_tokens.py` | Rotating opaque refresh tokens (SHA-256 hashed), reuse detection | `models` | — |
| `services/access.py` | Role ranks and "what is this user's role on workspace / case" | `models` | — |
| `services/audit.py` | `record(session, action, …)` adds an `audit_events` row | `models` | commit (the caller's transaction does) |
| `services/storage.py` | Evidence file paths `{UPLOAD_DIR}/{case_id}/{doc_id}_{safe_name}`, filename sanitising | `core` | — |
| `services/case_index_registry.py` | One pipeline bundle per case, lazily loaded from Postgres, LRU-cached, with a per-case lock | `pipeline`, `services.index_store` | be bypassed. Routes get pipelines only through it |
| `services/index_store.py` | Save/load a case's pipeline state to/from Postgres (snapshot replace) | `models`, `pipeline` (only `export_state`/`load_state`) | contain algorithm logic |
| `pipeline/llm_provider.py` | **The only module that talks to the LLM or the embedding service.** It handles concurrency limits, timeouts, retries (rate limits / transient errors) and nomic task prefixes | `core` | be skipped. Every other module calls `generate()` / `embed()` |
| `pipeline/vectors.py` | Shared vector helpers: `embed_or_fallback` (embedding with the D6 random fallback), `EmbeddingIndex` (cosine search as one NumPy operation), `EMBED_DIM` | `llm_provider` | — |
| `pipeline/document_processor.py` | Extracts file → plain text | PyMuPDF, PyPDF2, python-docx | chunk or index |
| `pipeline/graphrag_indexer.py` | Chunking, entity/relation extraction, graph, communities, graph context | `llm_provider` | know about HTTP or the database |
| `pipeline/raptor_runner.py` | RAPTOR tree build and retrieval | `llm_provider`, `vectors` | know about the database |
| `pipeline/hippo_retriever.py` | HiPPO pyramid build and retrieval | `vectors` (embed only) | call `generate` |
| `pipeline/query_engine.py` | Context assembly per method, answer prompt, entity linking, chat history (in-memory, per case) | the three retrievers, `llm_provider` | mutate the indexes |
| `middleware/`, `utils/` | Empty placeholders | — | — |

### Migrations (`alembic/`)

`alembic/env.py` reads `DATABASE_URL` from `.env` through `core.config` (never from
`alembic.ini`). Revisions live in `alembic/versions/`. **Never edit an applied migration; add a new one.**

### Evaluation (`evaluation/eval.py`)

A standalone CLI. It builds its **own** in-memory pipeline instances, needs no database and no
login, indexes `--docs_dir`, runs the QA pairs and writes a JSON report.

### Frontend (`frontend/src/app/`)

| Unit | Responsibility |
|---|---|
| `services/api.service.ts` | **The only place that makes HTTP calls.** Holds the TypeScript mirrors of the backend schemas |
| `services/auth.service.ts` | Session signals (`user`, `isLoggedIn`), access token in memory, `restoreSession()` (APP_INITIALIZER), shared refresh |
| `services/case-context.service.ts` | The open case and the user's role on it (`canEdit`, `isLead`); read by the case components |
| `services/api-errors.ts` | Turns API errors (incl. fastapi-users error codes) into sentences |
| `auth/auth.interceptor.ts` | Adds `Authorization: Bearer`; on 401 refreshes once and retries |
| `auth/auth.guards.ts` | `authGuard`, `guestGuard` |
| `app.component.ts` | Global shell: top bar (brand, API status, user, sign out) + `<router-outlet>` |
| `pages/login.page.ts`, `register.page.ts`, `google-callback.page.ts` | Sign-in flows |
| `pages/cases-dashboard.page.ts` | Workspace switcher, case list with status filter, new case / new organization dialogs |
| `pages/case-workspace.page.ts` | One case: header (status, upload), tabs Graph / Ask / Analytics / Team, stats polling |
| `pages/workspace-members.page.ts` | Organization members (add by email, roles, leave, delete organization) |
| `components/sidebar.component.ts` | Evidence list and status, stats; upload/delete shown by role |
| `components/upload-modal.component.ts` | Drag-and-drop upload; `@Output() close` |
| `components/graph-view.component.ts` | D3 force graph, entity detail panel |
| `components/qa-panel.component.ts` | Chat UI over the case's stored history, method selector, suggestions, clear history (lead) |
| `components/case-team.component.ts` | Case details, case members, audit log, delete case |
| `components/google-button.component.ts` | "Continue with Google" (hidden when the server has no Google credentials) |

## 5. Data flow

### 5.1 Authentication

- **Password**: `POST /api/auth/register` (201, no login) → `POST /api/auth/jwt/login` (form
  `username`, `password`). On success the body has `{access_token, token_type, expires_in}`
  and a `refresh_token` cookie is set (`HttpOnly`, `SameSite=Lax`, `Path=/api/auth`, 7 days).
- **Google** (OAuth2 authorization-code flow, fastapi-users): the SPA calls
  `GET /api/auth/google/authorize` (sets a CSRF cookie) and navigates to the returned Google
  URL. Google redirects to `FRONTEND_URL/auth/google/callback?code&state`. That page calls
  `GET /api/auth/google/callback?code&state` with credentials, and the API verifies state + CSRF,
  exchanges the code, reads the email (People API), finds/links/creates the user and logs in as above.
  Accounts are linked by email (`associate_by_email=True`; Google only returns verified primary emails).
- **Access token**: JWT, 15 min, kept in memory by the SPA. **Refresh**: `POST /api/auth/refresh`
  rotates the cookie; presenting an already-rotated token revokes all of that user's tokens.
  On page load the SPA calls refresh to restore the session.
- **Sign-up hook**: every new user gets a personal workspace (owner).

### 5.2 Ingestion (`POST /api/cases/{case_id}/documents/upload`, investigator+)

1. Rejected with 400 if the case is `closed` or `archived`.
2. The file is streamed to `{UPLOAD_DIR}/{case_id}/{doc_id}_{sanitised name}` in 256 KB chunks
   while its **SHA-256** is computed. If it exceeds `MAX_FILE_SIZE_MB` (50): 413 and the file is deleted.
3. A `documents` row (`status="uploaded"`) and an audit event are committed; the `DocRecord` is
   returned **immediately**.
4. Background task `_run_indexing` gets the case's bundle from `CaseIndexRegistry` and holds
   the **case lock** (uploads to one case are indexed one at a time):
   1. status `indexing`; `extract_text`; `chunk_text` → **500-word windows, 80-word overlap**,
      chunk id = `"{doc_id}_c{idx}"`
   2. `graphrag.index_document(...)` first, then `asyncio.gather(raptor.build_tree, hippo.index_passages)`
      (decisions D4/D5). Louvain re-runs over the whole case graph; a community whose
      membership is unchanged **reuses its summary** (no LLM call), and summaries of communities
      that no longer exist are dropped, so LLM calls per upload don't grow with the case
   3. **one transaction**: insert the chunk rows, `save_case_state` (replace the case's index
      snapshot), status `indexed` + `chunk_count`, audit event
   4. on any exception: status `error` + message, audit event, and the bundle is **reloaded from
      Postgres** so the half-updated in-memory state is discarded
5. On server start, documents still `uploaded`/`indexing` are marked `error` ("interrupted by restart").

### 5.3 Query (`POST /api/cases/{case_id}/query`, viewer+)

1. 400 if the case has no documents.
2. The case's `QueryEngine` runs exactly as before: embed the question, `_build_context` per
   method (graph keyword match / RAPTOR collapsed tree / HiPPO multi-level, first 10 blocks),
   `_ANSWER_PROMPT` with the last 3 turns of **this case's** history, `generate(json_mode=True)`.
3. The turn is stored in `chat_messages` (user, method, top_k, answer, reasoning, confidence,
   entities, sources, **latency_ms**) with an audit event, and the `QueryResponse` is returned.
4. `GET /api/cases/{case_id}/chat` returns the stored history (oldest first);
   `DELETE …/chat` (lead) clears it and the engine's in-memory history.

### 5.4 Loading a case

`CaseIndexRegistry.get(case_id)` returns the cached bundle, or loads it: read the case's rows
(`index_store.load_case_state`), build fresh pipeline objects and call `load_state` on each.
The QueryEngine history is seeded with the last 3 stored chat turns. At most
`INDEX_CACHE_MAX_CASES` bundles are kept (least recently used is evicted, never one that is indexing).

## 6. Key interfaces

### 6.1 HTTP API contract (prefix `/api`)

These are **public interfaces**. Changing a path, field name, type or status code requires
owner approval (see `CLAUDE.md`). Keep `schemas/*.py` and `api.service.ts` in sync.
Missing/invalid token → **401**. Not allowed to see the workspace/case → **404** (existence is
not revealed). Allowed to see but role too low → **403**. Malformed UUID → 422.

**Roles.** Workspace: `member < admin < owner`. Case: `viewer < investigator < lead`.
A user's case role is their `case_members` row, raised to `lead` if they are owner/admin of the
case's workspace. Only workspace members can be added to a case.

| Method & path | Who | Request | Response | Errors |
|---|---|---|---|---|
| `GET /health` | anyone | — | `{status, api_key_set, llm_provider, llm_model, embed_provider, embed_model}` | — |
| `GET /auth/config` | anyone | — | `{google_enabled}` | — |
| `POST /auth/register` | anyone | `{email, password, full_name}` | **201** `UserRead` | 400 `REGISTER_USER_ALREADY_EXISTS` / `REGISTER_INVALID_PASSWORD`; 422 |
| `POST /auth/jwt/login` | anyone | form `username`, `password` | `TokenResponse` + refresh cookie | 400 `LOGIN_BAD_CREDENTIALS` |
| `POST /auth/jwt/logout` | user | — | 204; revokes **all** the user's refresh tokens | 401 |
| `GET /auth/google/authorize` | anyone | — | `{authorization_url}` + CSRF cookie | only mounted when Google is configured |
| `GET /auth/google/callback` | anyone | query `code`, `state` | `TokenResponse` + refresh cookie | 400 invalid state / `OAUTH_*` |
| `POST /auth/refresh` | cookie | — | `TokenResponse` + rotated cookie | 401 |
| `POST /auth/logout` | cookie | — | 204; revokes this browser's token, clears cookie | — |
| `GET/PATCH /users/me` | user | `UserUpdate` (`full_name`, `password`, `email`) | `UserRead` | 400 |
| `GET /workspaces` | user | — | `WorkspaceOut[]` (personal first) | — |
| `POST /workspaces` | user | `{name}` | **201** `WorkspaceOut` (organization, caller = owner) | — |
| `GET /workspaces/{id}` | member | — | `WorkspaceOut` | 404 |
| `PATCH /workspaces/{id}` | owner | `{name}` | `WorkspaceOut` | 403/404 |
| `DELETE /workspaces/{id}` | owner | — | 204 (deletes cases + files) | 400 personal |
| `GET /workspaces/{id}/members` | member | — | `WorkspaceMemberOut[]` | 404 |
| `POST /workspaces/{id}/members` | admin | `{email, role: admin\|member}` | **201** | 400 personal; 404 unknown email; 409 already member |
| `PATCH /workspaces/{id}/members/{user_id}` | admin | `{role}` | `WorkspaceMemberOut` | 403 owner |
| `DELETE /workspaces/{id}/members/{user_id}` | admin, or self | — | 204 (also removes their case memberships there) | 400 owner |
| `GET /workspaces/{id}/cases` | member | `?status=` | `CaseOut[]` (admins: all; members: their cases) | — |
| `POST /workspaces/{id}/cases` | member | `CaseCreate` | **201** `CaseOut` (caller = lead) | 409 duplicate reference code |
| `GET /cases/{case_id}` | viewer | — | `CaseOut` | 404 |
| `PATCH /cases/{case_id}` | investigator | `CaseUpdate` | `CaseOut` (`closed_at` set/cleared with status) | 409 |
| `DELETE /cases/{case_id}` | lead | — | 204 (cascade + files + cache) | — |
| `GET /cases/{case_id}/members` | viewer | — | `CaseMemberOut[]` | — |
| `POST /cases/{case_id}/members` | lead | `{email, role}` | **201** | 404 unknown; 400 not in workspace; 409 |
| `PATCH /cases/{case_id}/members/{user_id}` | lead | `{role}` | `CaseMemberOut` | 400 last lead |
| `DELETE /cases/{case_id}/members/{user_id}` | lead, or self | — | 204 | 400 last lead |
| `GET /cases/{case_id}/audit` | lead | `?limit=50` (≤200) | `AuditEventOut[]` newest first | — |
| `POST /cases/{case_id}/documents/upload` | investigator | multipart `file` | `DocRecord` | 400 closed case; 413; 500 key missing |
| `GET /cases/{case_id}/documents` | viewer | — | `DocRecord[]` | — |
| `GET /cases/{case_id}/documents/{doc_id}` | viewer | — | `DocRecord` | 404 |
| `DELETE /cases/{case_id}/documents/{doc_id}` | lead | — | `{deleted: doc_id}` | 404 |
| `GET /cases/{case_id}/graph` | viewer | — | `GraphData` | — |
| `GET /cases/{case_id}/graph/stats` | viewer | — | `GraphStats` | — |
| `GET /cases/{case_id}/graph/entity/{entity_id}` | viewer | — | `EntityDetail` | 404 |
| `POST /cases/{case_id}/query` | viewer | `QueryRequest` | `QueryResponse` | 400 no documents |
| `GET /cases/{case_id}/query/suggestions` | viewer | — | `{suggestions: string[]}` (≤5) | — |
| `GET /cases/{case_id}/chat` | viewer | `?limit=100` (≤500) | `ChatMessageOut[]` oldest first | — |
| `DELETE /cases/{case_id}/chat` | lead | — | 204 | — |

fastapi-users also mounts `GET/PATCH/DELETE /users/{id}` for superusers; no UI uses them.

### 6.2 Schemas

```python
# schemas/auth.py
class UserRead(BaseUser[uuid.UUID]): full_name: str; created_at: datetime   # + id, email, is_active, is_superuser, is_verified
class UserCreate(BaseUserCreate):    full_name: str        # password ≥ 8 chars, must not contain the email name
class TokenResponse(BaseModel):      access_token: str; token_type: "bearer"; expires_in: int

# schemas/workspaces.py
class WorkspaceOut:       id, name, kind ("personal"|"organization"), my_role, member_count, created_at
class WorkspaceMemberOut: user_id, email, full_name, role, created_at

# schemas/cases.py
class CaseCreate: title, description="", reference_code=None, priority="medium"
class CaseUpdate: title?, description?, reference_code?, status?, priority?     # only sent fields change
class CaseOut:    id, workspace_id, reference_code, title, description, status, priority,
                  created_by, created_at, updated_at, closed_at, my_role, document_count
class CaseMemberOut: user_id, email, full_name, role ("lead"|"investigator"|"viewer"), created_at

# schemas/documents.py
class DocRecord: id, filename, content_type, size_bytes, status, error, chunks,
                 case_id, sha256, uploaded_by, created_at

# schemas/query.py — QueryRequest / QueryResponse unchanged
class ChatMessageOut: id, question, method, top_k, answer, reasoning, confidence, entities,
                      sources, latency_ms, user_id, user_name, created_at

# schemas/audit.py
class AuditEventOut: id, action, user_id, user_email, target_type, target_id, details, created_at
```

`GraphData`, `GraphStats`, `EntityDetail` are plain dicts whose shape is fixed by `api.service.ts`
(unchanged). Entity types: `PERSON, ORGANIZATION, LOCATION, DATE, EVENT, CONCEPT, PRODUCT, LAW,
DISEASE, DRUG, OTHER`.

### 6.3 Database (PostgreSQL 18 + pgvector, database `spl3`)

Conventions: UUID primary keys (generated in Python), `TIMESTAMPTZ`, enums as `TEXT + CHECK`
(not Postgres ENUM types), every FK indexed, `ON DELETE CASCADE` for children, `SET NULL` for
"who did it" columns, named constraints (naming convention in `core/database.py`), case-insensitive
unique email (`lower(email)`).

| Group | Tables |
|---|---|
| Identity | `users` (fastapi-users columns + `full_name`, `last_login_at`), `oauth_accounts`, `refresh_tokens` (hash only) |
| Tenancy | `workspaces` (`personal`/`organization`), `workspace_members` (owner/admin/member) |
| Investigation | `cases` (status, priority, reference code unique per workspace), `case_members` (lead/investigator/viewer), `documents` (stored path, sha256, status), `chunks` |
| Index (derived, per case) | `entities`, `entity_mentions`, `relationships`, `communities`, `raptor_nodes` (`vector(768)`), `hippo_nodes` (`vector(768)`), `case_index_meta` |
| Activity | `chat_messages` (chat history + query log), `audit_events` |

Index tables have an FK to `cases` but **no FK to documents/chunks** (deleting a document doesn't
un-index it). `seq` columns keep the pipeline's insertion order, so a reload rebuilds identical
objects (graph edge weights and "first relation" are replayed from `relationships` in order).

### 6.4 Internal Python interfaces

```python
# pipeline/llm_provider.py   — the only gateway to the models
async def generate(prompt: str, json_mode: bool = False, temperature: float = 0.1) -> str
async def embed(text: str, task_type: str = "retrieval_document") -> List[float]   # 768-d; input truncated to 2048 chars
    # task_type "retrieval_document" | "retrieval_query" → nomic prefixes "search_document: " / "search_query: "
def active_api_key_set() -> bool
def provider_info() -> dict
# Concurrency: LLM semaphore 3, embed semaphore 5. Timeouts (per attempt): LLM 45 s, embed 30 s.
# generate() retries RateLimitError / InternalServerError / APIConnectionError up to 5 times,
# waiting Groq's retry-after or 2 s, 4 s, 8 s… (max 30 s). Other errors (e.g. 400) raise at once.

# pipeline/vectors.py
EMBED_DIM = 768
async def embed_or_fallback(text: str, owner: str, task_type: str = "retrieval_document") -> List[float]
class EmbeddingIndex:   # built lazily by RAPTOR/HiPPO, dropped whenever their nodes change
    def cosine(query, ids=None) -> Tuple[List[str], np.ndarray]

# pipeline/document_processor.py
async def extract_text(file_path: str, content_type: str = "") -> str   # returns "" on failure

# pipeline/graphrag_indexer.py
Chunk = {"id": str, "text": str, "doc_id": str}
class GraphRAGIndexer(chunk_size=500, overlap=80):
    def chunk_text(text: str, doc_id: str) -> List[Chunk]
    async def index_document(doc_id: str, text: str, chunks: Optional[List[Chunk]] = None) -> Dict
    def get_graph_data() -> Dict; def get_stats() -> Dict
    def get_context_for_query(query: str, max_entities: int = 15) -> str
    def export_state() -> Dict; def load_state(state: Dict) -> None          # NEW (persistence)

# pipeline/raptor_runner.py
class RaptorRunner(max_levels=3, target_cluster_size=5):
    async def build_tree(chunks) -> Dict; def retrieve(query_embedding, top_k=6) -> List[Dict]
    def get_stats() -> Dict; def export_state() -> Dict; def load_state(state) -> None

# pipeline/hippo_retriever.py
class HippoRetriever(pool_size=2, max_levels=4):
    async def index_passages(passages) -> Dict; def retrieve(query_embedding, top_k=5, level=0)
    def retrieve_multi_level(query_embedding, top_k=5); def get_stats() -> Dict
    def export_state() -> Dict; def load_state(state) -> None

# pipeline/query_engine.py
class QueryEngine(graphrag, raptor, hippo):
    async def query(question: str, method: Method = "hybrid", top_k: int = 6) -> Dict
    def get_suggestions() -> List[str]
    def export_state() -> Dict; def load_state(state) -> None   # history only

# services/case_index_registry.py
class CaseIndexRegistry(max_cases=INDEX_CACHE_MAX_CASES):
    async def get(case_id) -> CaseBundle          # graphrag, raptor, hippo, engine, lock
    async def reload(case_id, bundle) -> None; def evict(case_id) -> None
```

The unused `api_key: str = ""` constructor parameters are left over from the Gemini era.
They are kept only for signature compatibility.

### 6.5 Evaluation CLI

```
python -m evaluation.eval --qa_pairs <file.json> [--docs_dir DIR] [--method graphrag|raptor|hippo|hybrid] [--output eval_report.json]
# qa_pairs format: [{"question": "...", "reference": "..."}]
```

## 7. Tech choices (decided)

| Concern | Choice | Why / notes |
|---|---|---|
| Backend | Python 3.11, FastAPI, uvicorn, pydantic v2, pydantic-settings | Async-first; auto-generated OpenAPI docs at `/docs` |
| Database | **PostgreSQL 18** + **pgvector** (embeddings as `vector(768)`), **SQLAlchemy 2.0 async** + **asyncpg**, **Alembic** migrations | Owner decisions P4/P5. Retrieval still runs in NumPy, so results match the in-memory pipeline |
| Auth | **fastapi-users** (register, login, JWT, Google OAuth via httpx-oauth; argon2 via pwdlib) + own rotating refresh-token cookie | Owner decision P1. fastapi-users has no refresh tokens |
| Google sign-in | OAuth2 **authorization-code** flow, redirect back to the SPA | Owner decision P2. Needs client ID + secret and the People API |
| Tenancy | Workspaces (personal + organization) + **per-case member lists** | Owner decision P7 |
| LLM | **Groq** (`GROQ_MODEL`, default `llama-3.3-70b-versatile`) | See §8 item 1: that model is no longer available to the project key |
| Embeddings | **Ollama** `nomic-embed-text`, local, 768-d | Free and offline; fits in 4 GB VRAM |
| Graph | NetworkX + `nx.community.louvain_communities(seed=42)` | Deterministic communities |
| Clustering | scikit-learn `GaussianMixture(covariance_type="full", random_state=42)` | As in the RAPTOR paper (no UMAP) |
| PDF / DOCX | PyMuPDF with a PyPDF2 fallback; python-docx | |
| Async model | Blocking calls in `asyncio.to_thread`; fan-out with `asyncio.gather(..., return_exceptions=True)` | |
| Frontend | Angular 17 standalone components, signals, router with lazy pages, functional guards and interceptor, D3 v7 | Templates and styles inline in each `.ts` |
| Styling | Dark theme, CSS custom properties and shared classes in `src/styles.scss` | **[OPEN]** dark vs the light palette in `assets/frontend_design_choices/color_theme.md` |
| Eval metrics | `rouge-score`, `nltk` BLEU-1, custom entity recall and faithfulness | |
| Determinism | Fixed seeds (42) for Louvain and GMM | Makes evaluation runs comparable |

## 8. Known limitations and gaps (as of 2026-10-04)

These are recorded so nobody "discovers" them twice. Fixing any of them is a separate,
approved task.

1. **The default Groq model is gone.** `llama-3.3-70b-versatile` returns 404 for the project's
   key; every LLM call then degrades (no entities, error answers). Set `GROQ_MODEL` in `.env`
   to an available model (tested: `openai/gpt-oss-120b`, which works but sometimes fails Groq's
   JSON validation on extraction). **[OPEN]** which model to standardise on.
2. **Delete does not un-index.** A deleted document's entities, edges, RAPTOR and HiPPO nodes stay
   in the case's indexes and still appear in answers and the graph.
3. **Snapshot replace on every index.** After each document, all of the case's index rows are
   deleted and re-inserted. Simple and consistent, but the cost grows with case size.
4. **Silent embedding fallback.** If Ollama is down, `_embed` returns a **random** unit vector
   (seeded by `hash(text)`), so retrieval quality silently collapses. Check the logs for `random fallback`.
5. **API-key check is ineffective.** `Settings.GROQ_API_KEY` defaults to the literal string
   `"GROQ_API_KEY"`, so `active_api_key_set()` is always true and the 500 check in `upload` never fires.
6. **The frontend bypasses the dev proxy.** `ApiService.base` is hard-coded to
   `http://localhost:8000/api` and relies on CORS (cookies work because both are on `localhost`).
7. **Chat history is per case, shared by all its members**, both stored and in the prompt
   (the last 3 turns of anyone on the case).
8. **`POST /query` only checks that the case has a document**, not that one reached `indexed`.
9. **RAPTOR `root_ids` is overwritten** on each document. Retrieval is unaffected.
10. **HiPPO `levels` reflects only the last document**, while `nodes` holds all of them, so
    `retrieve_multi_level` searches only that document's pyramid. Pre-existing; persisted as-is.
11. **Indexes built before 2026-10-04 lack the nomic task prefixes.** Queries are now embedded
    with `search_query: `, so re-upload old evidence to get matching document vectors.
12. **Embeddings are stored as float32** (pgvector). After a reload, similarity scores can differ
    from the pre-reload in-memory float64 values in the last decimals.
13. **Google OAuth tokens** are stored in `oauth_accounts` in plain text (fastapi-users design).
    Only basic profile/email scopes are requested.
14. **No rate limiting, email verification or password reset**, and no CSRF token on cookie
    endpoints (mitigated by `SameSite=Lax` and `Path=/api/auth`; refresh only returns a token in the body).
15. **The README is stale.** It says Gemini and `ng serve`.
16. **No automated tests** exist yet.

## 9. Decisions already made

- **D1 — One gateway for models.** All LLM and embedding calls go through `pipeline/llm_provider.py`.
- **D2 — (superseded by D11)** Global pipeline singletons.
- **D3 — Indexing is async and backgrounded.** Upload returns at once; the client polls status.
- **D4 — Shared chunks.** All three indexes consume the **same** chunk list from
  `GraphRAGIndexer.chunk_text`, so retrieval comparisons are fair.
- **D5 — GraphRAG runs first, then RAPTOR and HiPPO in parallel.**
- **D6 — Degrade, don't crash.** Pipeline failures are logged and replaced by a fallback.
  A document only gets `error` status when the whole indexing task throws.
- **D7 — "HiPPO" is hierarchical passage pooling**, not the paper's HippoRAG (OpenIE + PPR).
  **[OPEN]** whether to implement PPR for the final.
- **D8 — Graph context uses keyword matching**, not embeddings.
- **D9 — The LLM returns structured JSON** (`json_mode=True`) for extraction and answers.
- **D10 — Fixed seeds** for anything stochastic.
- **D11 — One pipeline bundle per case**, loaded lazily from Postgres and LRU-cached, with a
  per-case indexing lock.
- **D12 — The pipeline is persistence-agnostic.** It only exposes `export_state` / `load_state`
  (plain dicts); `services/index_store.py` maps them to rows.
- **D13 — Access = workspace membership + per-case role.** Non-members get 404; workspace
  owners/admins are implicit leads on all their cases.
- **D14 — Tokens.** Short-lived JWT access token in memory + rotating httpOnly refresh cookie with
  reuse detection. Every turn of the chat is persisted (`chat_messages`) with its latency.

## 10. Open questions for the owner

- [ ] Which Groq model replaces `llama-3.3-70b-versatile` (§8.1)?
- [ ] Should delete un-index (rebuild the case from its remaining documents)?
- [ ] Implement true HippoRAG (PPR over the entity graph) or keep pooling (D7)?
- [ ] Which test framework (pytest + pytest-asyncio + httpx?) — needs dependency approval.
- [ ] Dark theme (current) vs the light palette in `color_theme.md`?
- [ ] Should the frontend use relative `/api` and the proxy instead of the hard-coded host?

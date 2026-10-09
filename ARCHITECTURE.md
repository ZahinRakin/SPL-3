# GraphRAG Investigations — Architecture & Spec

> Status: **DRAFT — pending owner review.** Updated 2026-10-04 for the investigation-tool
> extension (auth, cases, PostgreSQL), 2026-10-08 for the cascade redesign (three parallel
> retrievers → one RAPTOR → GraphRAG → HippoRAG pipeline, two query modes), and 2026-10-09 for the
> post-experiment accuracy fixes (RAPTOR tree, embedding limit, entity identity, hybrid ranking). The original plan is
> in `docs/EXTENSION_PLAN.md`.
> Sections marked **[OPEN]** are things the owner still needs to decide.
> When code and this document disagree, fix one of them; don't leave the gap.

---

## 1. Goal

A full-stack **investigation tool** for the SPL-3 course project. People and organizations
register, open **cases**, and upload evidence files (PDF / DOCX / TXT / HTML) to each case.
Every case gets its own indexes, built by one cascaded pipeline, and its own Q&A chat over its evidence.

The project has to show:

1. **Three research methods combined into one cascade**, based on the papers in
   `../assets/papers/`. Each stage has one job:
   1. **RAPTOR — enrich**: a recursive summary tree built with GMM clustering. Its nodes (chunks
      and summaries) are the case's passages.
   2. **GraphRAG — structure**: one LLM-extracted knowledge graph over the chunks **and** the
      RAPTOR summaries, with Louvain communities and community summaries (written from the
      members' descriptions and relations; `refined` mode adds the most relevant ones to the context).
   3. **HippoRAG — rank**: Personalized PageRank over that graph, seeded from the question's
      entities, scores the passages; only the top-k reach the LLM.
2. **Two query modes**: `standard` (plain RAG: the top-k most similar chunks, the baseline) and
   `refined` (the cascade).
3. **A real-life use**: multi-user, access-controlled investigation cases stored in PostgreSQL.
4. **An evaluation of refined vs standard** using ROUGE, BLEU-1, entity recall and latency.
   Every chat turn stores its mode and latency to support that comparison.

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
  Local models must be about 4B parameters or smaller. Generation runs on OpenRouter; only
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
   pipeline/  RaptorRunner → GraphRAGIndexer → HippoRetriever (PPR) · QueryEngine  (one bundle per case)
              llm_provider.generate / embed
        │                         │                          │
  PostgreSQL 18 + pgvector   OpenRouter (OpenAI API)    Ollama (localhost:11434)
  (database spl3)            (LLM_MODEL)                nomic-embed-text (768-d)
```

## 4. Module boundaries

All paths are relative to `graphrag-project/`.

### Backend (`backend/`)

| Module | Responsibility | May depend on | Must NOT |
|---|---|---|---|
| `app/main.py` | Creates the FastAPI app, CORS, `/api/health`, lifespan (JWT-secret check, DB ping, marks interrupted indexing as `error`) | `core`, `api`, `models`, `pipeline.llm_provider` | contain business logic |
| `app/dependencies.py` | `require_workspace_role(min)` / `require_case_role(min)` access dependencies; `get_case_index_registry()` | `services`, `models` | be bypassed. Every protected route declares its minimum role here |
| `api/*.py` | HTTP layer: validation, status codes, commits, background tasks | `schemas`, `core`, `models`, `services`, `app.dependencies`, `pipeline.document_processor` | call the LLM API or Ollama directly, or hold algorithm logic |
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
| `services/case_index_registry.py` | One pipeline bundle per case, lazily loaded from Postgres, LRU-cached, with a per-case lock; fills `QueryEngine.doc_names` from `documents` | `pipeline`, `services.index_store`, `models` | be bypassed. Routes get pipelines only through it |
| `services/index_store.py` | Save/load a case's pipeline state to/from Postgres (snapshot replace) | `models`, `pipeline` (only `export_state`/`load_state`) | contain algorithm logic |
| `pipeline/llm_provider.py` | **The only module that talks to the LLM or the embedding service.** It handles concurrency limits, timeouts, retries (rate limits / transient errors) and nomic task prefixes | `core` | be skipped. Every other module calls `generate()` / `embed()` |
| `pipeline/vectors.py` | Shared vector helpers: `embed_or_fallback` (embedding with the D6 random fallback), `EmbeddingIndex` (cosine search as one NumPy operation), `EMBED_DIM` | `llm_provider` | — |
| `pipeline/document_processor.py` | Extracts file → plain text | PyMuPDF, PyPDF2, python-docx | chunk or index |
| `pipeline/graphrag_indexer.py` | Chunking; stage 2: entity/relation extraction over passages, graph, communities; question-entity matching and entity descriptions for the prompt | `llm_provider` | know about HTTP or the database |
| `pipeline/raptor_runner.py` | Stage 1: RAPTOR tree build (the case's passages); similarity search over the tree or one level (Standard mode) | `llm_provider`, `vectors` | know about the database |
| `pipeline/hippo_retriever.py` | Stage 3: HippoRAG Personalized PageRank ranking at query time. Stores nothing | `graphrag_indexer`, `raptor_runner` (read only) | call `generate`, mutate the indexes |
| `pipeline/query_engine.py` | Context assembly per mode (`standard` / `refined`), answer prompt, entity linking, chat history (in-memory, per case) | the three stages, `llm_provider` | mutate the indexes |
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
| `components/qa-panel.component.ts` | Chat UI over the case's stored history, Refined / Standard mode selector, suggestions, clear history (lead) |
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
   1. status `indexing`; `extract_text`; `chunk_text` → **250-word windows, 40-word overlap**,
      chunk id = `"{doc_id}_c{idx}"`
   2. **The cascade** (decision D5), one stage after another:
      `raptor.build_tree(chunks)` → `passages = raptor.passages(doc_id)` (the doc's chunks + summaries,
      ids = RAPTOR node ids) → `graphrag.index_document(doc_id, text, passages)`. HippoRAG has no
      indexing step. A GraphRAG failure fails the document; a RAPTOR failure is logged and the graph
      is built from the plain chunks (D6). Measured 2026-10-09: a 764-word document took **~13 s**
      (4 chunks + 1 summary → 182 entities). RAPTOR only summarises clusters of ≥ 2 nodes (≥ 3
      chunks at level 1), from the children's full text, and stops at one root. Relationship
      endpoints that don't exactly match an extracted entity are resolved (fuzzy, or a same-named
      entity in the case) or created as `OTHER` entities; the per-document counts are logged.
      Entities are identified by `(name, type)`, and likely aliases of the same type get a
      `SAME_AS` edge instead of a merge. Louvain re-runs over the whole case graph; a community
      whose membership is unchanged **reuses its summary** (no LLM call), and summaries of
      communities that no longer exist are dropped, so LLM calls per upload don't grow with the case
   3. **one transaction**: `save_case_state` (replace the case's index
      snapshot), status `indexed` + `chunk_count`, audit event
   4. on any exception: status `error` + message, audit event, and the bundle is **reloaded from
      Postgres** so the half-updated in-memory state is discarded
5. On server start, documents still `uploaded`/`indexing` are marked `error` ("interrupted by restart").

### 5.3 Query (`POST /api/cases/{case_id}/query`, viewer+)

1. 400 if the case has no documents.
2. The case's `QueryEngine` embeds the question and builds the context for the mode:
   - `standard`: the top-k RAPTOR level-0 nodes (chunks) by cosine similarity. Plain RAG.
   - `refined`: `HippoRetriever.retrieve`. Seeds = entities named in the question
     (`graphrag.match_entities`, weight = share of the name's words found) + the entities of the 3
     most similar chunks (weak, spread over each chunk's entities); every seed is divided by the
     number of chunks mentioning it (node specificity). `nx.pagerank(alpha=0.5, personalization=seeds)`
     runs over the entity graph. Each chunk scores `cosine + 0.5 · (its entities' PageRank / the best
     chunk's)`; RAPTOR summaries score `cosine − 0.03 · level` and take at most 2 of the top-k slots.
     The top-k passages, a `[KNOWLEDGE GRAPH]` block describing the top PageRank entities and
     `[COMMUNITY SUMMARY]` blocks form the context. Communities score `cosine(question, summary) +
     0.5 · (their members' PageRank mass / the best community's)`; a specific question gets the top 2,
     a broad one (keyword cues such as "overall", "themes", "across", "all documents", "key findings")
     the top 6, with each document's best community first so one large document can't fill every slot.
     Summary embeddings are computed at query time and cached in memory until the summaries change.
     With no seeds the PageRank term is 0, so the ranking is plain similarity (D6).

   In both modes every context block names its source document
   (`[PASSAGE — source: <file name>]`; the registry loads the case's file names into
   `QueryEngine.doc_names`, and the evaluation's doc ids are already file names). Then `_ANSWER_PROMPT` with the last 3 turns of **this case's** history, `generate(json_mode=True)`.
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

# schemas/query.py
class QueryRequest:  question, method: "standard"|"refined" = "refined", top_k=6   # 422 for any other method
class QueryResponse: answer, entities, sources, confidence, reasoning, method
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
| Investigation | `cases` (status, priority, reference code unique per workspace), `case_members` (lead/investigator/viewer), `documents` (stored path, sha256, status, `chunk_count`) |
| Index (derived, per case) — shaped like the cascade | `entities.normalized_key` is `"<lower-case name>|<TYPE>"` (since 2026-10-09; one entity per name and type) · **`passages`** (stage 1: RAPTOR nodes; `level` 0 = chunk, > 0 = summary; `vector(768)`; the only text the LLM sees) · **`entities`**, **`relationships`**, **`communities`** (stage 2: the GraphRAG graph) · **`entity_mentions`** (`entity_id` → `passage_id`, FK to both, cascade: the links HippoRAG scores passages by). Stage 3 (HippoRAG) has no table |
| Activity | `chat_messages` (chat history + query log; `method IN ('standard','refined')`), `audit_events` |

Migrations of 2026-10-08: `3c9e2a7d41f0` dropped `hippo_nodes`, set the chat `method` check to
`standard`/`refined` and deleted chat rows with the old methods. `80d2d067ef08` renamed
`raptor_nodes` → `passages`, replaced `entity_mentions.chunk_id` (text, no FK) with `passage_id`
(UUID FK), dropped the write-only `chunks` table and `case_index_meta` (unused RAPTOR root ids),
emptied every case's index and marked existing documents `error` ("re-upload").

Index tables have an FK to `cases` but **no FK to documents** (deleting a document doesn't
un-index it). `seq` columns keep the pipeline's insertion order, so a reload rebuilds identical
objects (graph edge weights and "first relation" are replayed from `relationships` in order).

### 6.4 Internal Python interfaces

```python
# pipeline/llm_provider.py   — the only gateway to the models
async def generate(prompt: str, json_mode: bool = False, temperature: float = 0.1) -> str
async def embed(text: str, task_type: str = "retrieval_document") -> List[float]   # 768-d; input truncated to 6000 chars (~1.5K of the model's 2K tokens), logged
    # task_type "retrieval_document" | "retrieval_query" → nomic prefixes "search_document: " / "search_query: "
def active_api_key_set() -> bool
def provider_info() -> dict
# Concurrency: LLM semaphore LLM_MAX_CONCURRENCY (16), embed semaphore 2 batch requests. Timeouts (per attempt): LLM 45 s, embed 30 s.
# gpt-oss models are called with reasoning_effort="low" (see §8 item 1).
# generate() retries RateLimitError / InternalServerError / APIConnectionError up to 5 times,
# waiting the provider's retry-after or 2 s, 4 s, 8 s… (max 30 s). Other errors (e.g. 400, 402 out of credits) raise at once.
# The client is openai.AsyncOpenAI(base_url=LLM_BASE_URL); concurrency = LLM_MAX_CONCURRENCY (default 8).
# On OpenRouter every request sets provider.require_parameters=true, so it is only routed to
# providers that support JSON mode and reasoning effort.

async def embed_many(texts: List[str], task_type: str = "retrieval_document") -> List[List[float]]
    # Ollama /api/embed, 32 texts per request (~16x faster than one request per text); vectors L2-normalised.
    # OLLAMA_BASE_URL "localhost" is rewritten to 127.0.0.1 (Windows IPv6 fallback added ~2 s per request).
# OpenRouter routing: LLM_PROVIDER_SORT="throughput" picks the fastest host (~400-700 tok/s vs 18-44 tok/s
# for the cheapest), which is what makes indexing take seconds instead of minutes.

# pipeline/vectors.py
EMBED_DIM = 768
async def embed_or_fallback(text: str, owner: str, task_type: str = "retrieval_document") -> List[float]
async def embed_many_or_fallback(texts: List[str], owner: str, task_type: str = "retrieval_document") -> List[List[float]]
def set_strict_embeddings(strict: bool) -> None   # True (the evaluation CLIs): failures raise instead of falling back
class EmbeddingIndex:   # built lazily by RAPTOR, dropped whenever its nodes change
    def cosine(query, ids=None) -> Tuple[List[str], np.ndarray]

# pipeline/document_processor.py
async def extract_text(file_path: str, content_type: str = "") -> str   # returns "" on failure

# pipeline/graphrag_indexer.py
Chunk = {"id": str, "text": str, "doc_id": str}
class GraphRAGIndexer(chunk_size=250, overlap=40):
    def chunk_text(text: str, doc_id: str) -> List[Chunk]
    async def index_document(doc_id: str, text: str, chunks: Optional[List[Chunk]] = None) -> Dict
    def get_graph_data() -> Dict; def get_stats() -> Dict
    # index_document's `chunks` are passages: the RAPTOR chunks + summaries of the document
    def match_entities(question: str) -> Dict[str, float]          # entity id → name-word overlap (≥ 0.5)
    def describe_entities(entity_ids: List[str], max_relations: int = 4) -> str
    def export_state() -> Dict; def load_state(state: Dict) -> None

# pipeline/raptor_runner.py
class RaptorRunner(max_levels=3, target_cluster_size=5):
    async def build_tree(chunks) -> Dict
    def retrieve(query_embedding, top_k=6, level: Optional[int] = None) -> List[Dict]   # level=0: chunks only
    def passages(doc_id: str) -> List[Dict]                         # {id, text, doc_id, level}
    def get_stats() -> Dict; def export_state() -> Dict; def load_state(state) -> None

# pipeline/hippo_retriever.py   — stateless; no export_state/load_state
class HippoRetriever(graphrag, raptor, damping=0.5, seed_passages=3):
    async def retrieve(question: str, query_embedding, top_k: int = 6) -> Dict   # {passages, entities, communities: {cid: PageRank mass}}

# pipeline/query_engine.py
Method = Literal["standard", "refined"]
class QueryEngine(graphrag, raptor, hippo):
    async def query(question: str, method: Method = "refined", top_k: int = 6) -> Dict
    def get_suggestions() -> List[str]
    def export_state() -> Dict; def load_state(state) -> None   # history only
    doc_names: Dict[str, str]                                    # doc id → file name for source labels (set by the registry)

# services/case_index_registry.py
class CaseIndexRegistry(max_cases=INDEX_CACHE_MAX_CASES):
    async def get(case_id) -> CaseBundle          # graphrag, raptor, hippo, engine, lock
    async def reload(case_id, bundle) -> None; def evict(case_id) -> None
```

The unused `api_key: str = ""` constructor parameters are left over from the Gemini era.
They are kept only for signature compatibility.

### 6.5 Evaluation CLI

```
python -m evaluation.eval --qa_pairs <file.json> [--docs_dir DIR] [--method standard|refined] [--output eval_report.json]
# indexes --docs_dir with the same cascade as the API

python -m evaluation.compare validate|run|analyze --exp <experiment folder> [--reuse-index]
# Controlled Standard-vs-Refined experiment: pre-registered protocol, verbatim-checked QA set, one shared
# index, blinded grading, Wilcoxon signed-rank + bootstrap CI + Holm. Every artefact is written into the
# experiment folder (see evaluation/experiments/*/README.md). QueryEngine.query also returns `context`
# for this (not part of the HTTP response).
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
| LLM | **OpenRouter** `openai/gpt-oss-20b` via the `openai` package (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`) | Owner's choice (2026-10-06): $0.02/$0.10 per 1M tokens, no fixed per-minute cap on paid models, falls back across ~11 hosting providers. Any OpenAI-compatible API works by changing `.env`. A reasoning model, run at low effort |
| Embeddings | **Ollama** `nomic-embed-text`, local, 768-d | Free and offline; fits in 4 GB VRAM |
| Graph | NetworkX + `nx.community.louvain_communities(seed=42)` | Deterministic communities |
| Clustering | scikit-learn `PCA` to ≤ 10 dims, then `GaussianMixture(covariance_type="diag", random_state=42)`, about 5 nodes per cluster | The RAPTOR paper reduces dimensions first (UMAP); PCA does that without a new dependency. Full covariance in 768-d on a handful of points was underdetermined |
| PDF / DOCX | PyMuPDF with a PyPDF2 fallback; python-docx | |
| Async model | Blocking calls in `asyncio.to_thread`; fan-out with `asyncio.gather(..., return_exceptions=True)` | |
| Frontend | Angular 17 standalone components, signals, router with lazy pages, functional guards and interceptor, D3 v7 | Templates and styles inline in each `.ts` |
| Styling | Dark theme, CSS custom properties and shared classes in `src/styles.scss` | **[OPEN]** dark vs the light palette in `assets/frontend_design_choices/color_theme.md` |
| Eval metrics | `rouge-score`, `nltk` BLEU-1, custom entity recall and faithfulness | |
| Determinism | Fixed seeds (42) for Louvain and GMM | Makes evaluation runs comparable |

## 8. Known limitations and gaps (as of 2026-10-04)

These are recorded so nobody "discovers" them twice. Fixing any of them is a separate,
approved task.

1. **gpt-oss needs low reasoning effort.** At the default (medium) effort it often returns empty
   content, which fails JSON mode (`json_validate_failed`), so every extraction fails.
   `llm_provider` sends low reasoning effort for `gpt-oss` models (OpenRouter: `reasoning.effort`;
   other OpenAI-compatible APIs: `reasoning_effort`). Moved off Groq on 2026-10-06 because its
   paid tier wasn't available; the free tier (8K tokens/min) was too slow for indexing.
2. **Delete does not un-index.** A deleted document's entities, edges and RAPTOR nodes stay
   in the case's indexes and still appear in answers and the graph.
3. **Snapshot replace on every index.** After each document, all of the case's index rows are
   deleted and re-inserted. Simple and consistent, but the cost grows with case size.
4. **Embedding fallback.** If Ollama is down, the live app gets a **random** unit vector
   (seeded by `hash(text)`), so retrieval quality collapses. Since 2026-10-09 this is logged at
   `ERROR` ("RANDOM fallback"), and the evaluation CLIs turn on strict mode, so a failure stops the run.
5. **The API-key check only tests that `LLM_API_KEY` is non-empty**, not that it is valid or has
   credits. A bad key or empty balance shows up as failed extractions (401/402 in the log).
6. **The frontend bypasses the dev proxy.** `ApiService.base` is hard-coded to
   `http://localhost:8000/api` and relies on CORS (cookies work because both are on `localhost`).
7. **Chat history is per case, shared by all its members**, both stored and in the prompt
   (the last 3 turns of anyone on the case).
8. **`POST /query` only checks that the case has a document**, not that one reached `indexed`.
9. *(Gone 2026-10-08: RAPTOR `root_ids` was removed with `case_index_meta`.)*
10. **If RAPTOR fails for a document, its graph links are not stored.** The graph is then built
    from plain chunk ids, which aren't passages, so those entity mentions are skipped on save
    (logged as a warning) and `refined` falls back to similarity for that document's content.
11. *(Gone 2026-10-08: migration `80d2d067ef08` emptied all pre-cascade indexes.)*
12. **Embeddings are stored as float32** (pgvector). After a reload, similarity scores can differ
    from the pre-reload in-memory float64 values in the last decimals.
13. **Google OAuth tokens** are stored in `oauth_accounts` in plain text (fastapi-users design).
    Only basic profile/email scopes are requested.
14. **No rate limiting, email verification or password reset**, and no CSRF token on cookie
    endpoints (mitigated by `SameSite=Lax` and `Path=/api/auth`; refresh only returns a token in the body).
15. **The README is stale.** It says Gemini and `ng serve`.
16. *(Gone 2026-10-09: `tests/` holds an offline pytest suite for the pipeline, with the LLM and
    embeddings stubbed. The API, auth and database have no automated tests yet.)*
17. **Broad questions are still answered mostly from the passages,** and those follow similarity,
    so a larger document can dominate them. Community summaries cover every document, but passages don't.

## 9. Decisions already made

- **D1 — One gateway for models.** All LLM and embedding calls go through `pipeline/llm_provider.py`.
- **D2 — (superseded by D11)** Global pipeline singletons.
- **D3 — Indexing is async and backgrounded.** Upload returns at once; the client polls status.
- **D4 — Shared passages.** `GraphRAGIndexer.chunk_text` chunks once; RAPTOR's nodes (those chunks
  + their summaries) are the passages that GraphRAG extracts from and HippoRAG ranks.
- **D5 — The pipeline is a cascade** (changed 2026-10-08): RAPTOR → GraphRAG at indexing,
  HippoRAG PageRank at query time. Before that the three indexed in parallel as independent retrievers
  with a hybrid mode; the owner dropped per-method comparison for one coherent algorithm.
- **D6 — Degrade, don't crash.** Pipeline failures are logged and replaced by a fallback.
  A document only gets `error` status when the whole indexing task throws.
- **D7 — HippoRAG is the paper's Personalized PageRank** (changed 2026-10-08), over the GraphRAG
  graph rather than a separate OpenIE graph. The earlier mean-pooling "HiPPO" pyramid was removed.
- **D8 — Question entities are found by keyword matching** on entity names, not embeddings or an
  LLM call (keeps queries at one LLM call).
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

- [ ] Should delete un-index (rebuild the case from its remaining documents)?
- [x] Test framework: **pytest** (approved 2026-10-09), no pytest-asyncio (tests call `asyncio.run`).
- [ ] Dark theme (current) vs the light palette in `color_theme.md`?
- [ ] Should the frontend use relative `/api` and the proxy instead of the hard-coded host?

# Extension Plan: Investigation Workspace (Auth + Cases + PostgreSQL)

> **Implementation status (2026-10-04): Phases 0–7 implemented.** ARCHITECTURE.md is now the
> source of truth. The owner changed these decisions, so the build differs from the plan below:
> - **P1 → fastapi-users** (register, JWT login, Google OAuth, user manager). Login is
>   `POST /api/auth/jwt/login` (form), register returns 201 without logging in, and errors use
>   fastapi-users codes (400 `LOGIN_BAD_CREDENTIALS`, …). Our own rotating refresh cookie is kept (P3).
> - **P2 → OAuth2 authorization-code flow** (`/api/auth/google/authorize` + `/callback`, redirect
>   to the SPA at `/auth/google/callback`). Needs `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` and the People API.
> - **P5 → pgvector**: `raptor_nodes.embedding` / `hippo_nodes.embedding` are `vector(768)`.
> - **P7 → per-case member lists** (`case_members`: lead / investigator / viewer), kept together
>   with organizations. Workspace roles are owner / admin / member; owners/admins are implicit leads.
> - The database is named `spl3`. `query_logs` became **`chat_messages`**: the per-case chat
>   history the UI shows, also used as the query log (`GET`/`DELETE /api/cases/{id}/chat`).
> - Phase 9 (the naive-RAG baseline) is still future work.

> Original status: **PLAN.** Written 2026-10-04 against the code as of that date.
> Audience: the coding agent that implements it, and the owner who approves it.
> Read `CLAUDE.md` and `ARCHITECTURE.md` first. This plan **overrides** the non-goals in
> ARCHITECTURE.md §2 ("no auth", "no persistence", "no per-user isolation") once the owner
> approves it. Everything else in CLAUDE.md still applies.

---

## 0. How to use this document

- Work **phase by phase, in order** (§6). Each phase lists the files, the steps, and a
  **Done when** checklist. Do not start a phase until the previous one's checks pass.
- Code blocks are **skeletons**: they show names, signatures and the tricky parts. Fill in the
  obvious parts in the existing code style (CLAUDE.md "Coding style").
- If something in this plan contradicts the code, stop and ask the owner. Don't guess.
- Shell is **PowerShell**. In PowerShell 5.1 `curl` is an alias for `Invoke-WebRequest`, so
  always type **`curl.exe`** in the checks below.
- At the end of every phase, report: files changed, checks run, results, open issues.

---

## 1. Goal

Turn the demo into an **investigation tool**:

1. People sign up (email + password, or **Sign in with Google**) and log in.
2. Each person gets a **personal workspace**. They can also create **organization workspaces**
   and add other registered users with a role.
3. Inside a workspace, users create **cases** (an investigation: title, description, status,
   priority).
4. Inside a case, users upload **evidence files**. Each case has its **own** knowledge graph,
   RAPTOR tree, HiPPO pyramid and Q&A history. Nothing leaks between cases.
5. Everything is stored in **PostgreSQL** and survives a server restart.
6. All `/api` endpoints except health and the auth endpoints need a valid **JWT**.

### Future work this plan prepares for (do NOT build now)

The owner will later show that GraphRAG / RAPTOR / HiPPO / hybrid beat **traditional
("naive") RAG**. This plan prepares for that by:
- logging every query with its method, latency and confidence (`query_logs` table)
- keeping the eval CLI standalone and unchanged
- keeping the pipeline algorithms unchanged (retrieval still runs in NumPy, so numbers stay comparable)

See §9 for the outline of that later phase.

---

## 2. Decisions for owner sign-off

The plan uses the **default** in each row. The owner should confirm or change these before
Phase 1 starts.

| # | Decision | Default in this plan | Alternative |
|---|---|---|---|
| P1 | Auth approach | Hand-written FastAPI auth: **PyJWT** (tokens) + **pwdlib/argon2** (password hashing) + **google-auth** (verify Google ID tokens). About 300 lines and easy to explain in a viva | `fastapi-users` library: less code, but it's in maintenance mode and it's harder to explain what it does |
| P2 | Google sign-in flow | **Google Identity Services** button on the frontend gives an ID token. The backend verifies it and issues our own JWT. No redirect URIs, no client secret | Full OAuth2 authorization-code redirect flow (needs a client secret and a callback route) |
| P3 | Token storage | **Access token** (15 min) is kept in memory in Angular. **Refresh token** (7 days) is an `httpOnly` cookie, rotated on use, stored **hashed** in the DB | Single long-lived JWT in `localStorage` (simpler, but readable by any XSS) |
| P4 | ORM / driver / migrations | **SQLAlchemy 2.0 async + asyncpg + Alembic** | Raw SQL with asyncpg (no ORM) |
| P5 | Embedding storage | **`REAL[]` columns**. Retrieval keeps running in NumPy exactly as today | `pgvector` extension: no prebuilt Windows binaries for PG 18, so it needs compiling |
| P6 | Uploaded file bytes | Stored **on disk** (`data/uploads/{case_id}/…`); Postgres stores path, size and **SHA-256**. Everything else is in Postgres | File bytes in a `BYTEA` column (the owner said "store everything in Postgres"; this works but bloats the DB and backups) |
| P7 | Multi-tenancy | **Workspaces** (personal + organization) with roles `owner/admin/member/viewer`. Access to a case comes from workspace membership | Per-case member lists (more flexible, more tables, more UI) |
| P8 | Old global endpoints | **Removed** and replaced by case-scoped ones (`/api/cases/{case_id}/…`) | Keep them too (they would bypass case isolation) |
| P9 | Email verification / password reset | **Out of scope** (no email server). Google users are marked `email_verified=true` | Add an SMTP service later |

### Dependency approval needed (CLAUDE.md rule 2)

Python, to be added to `requirements.txt` in a new `# ── Database / Auth ──` section:

| Package | Why | No-dependency alternative |
|---|---|---|
| `sqlalchemy[asyncio]>=2.0.30` | ORM + async sessions | Raw SQL strings everywhere |
| `asyncpg>=0.29.0` | Async Postgres driver | none (some driver is required) |
| `alembic>=1.13.0` | Versioned schema migrations | Hand-run `.sql` files |
| `PyJWT>=2.8.0` | Sign and verify JWTs | Hand-rolled HMAC tokens (don't) |
| `pwdlib[argon2]>=0.2.1` | Argon2 password hashing (what the FastAPI docs recommend; `passlib` is unmaintained) | `hashlib.scrypt` from the stdlib (acceptable fallback) |
| `google-auth>=2.29.0` | Verify Google ID tokens | PyJWT + Google's JWKS endpoint |
| `requests>=2.31.0` | Transport that `google-auth` needs | — |
| `email-validator>=2.1.0` | Needed by Pydantic `EmailStr` | Regex check |

`google-auth` and `requests` are already installed in the venv as transitive dependencies, but
they must be listed explicitly.

**Frontend: no new npm packages.** The Google button is loaded from Google's script URL in
`index.html`. The router, guards and interceptors are already part of `@angular/router` and
`@angular/common/http`.

### Public interface changes needing approval (CLAUDE.md rule 1)

All of §5 (the HTTP API) and §7.2 (the TypeScript interfaces). The pipeline method signatures
in ARCHITECTURE.md §6.3 are **not** changed. Two methods are **added** to each pipeline
class (`export_state` / `load_state`, §6 Phase 4). The eval CLI is unchanged.

---

## 3. Target architecture

```
Angular SPA
  /login  /register  /cases  /cases/:caseId  /workspaces/:id/members
  AuthService (signals) ── authInterceptor (Bearer + 1 retry on 401) ── ApiService
                                   │  HTTP JSON /api/*
FastAPI
  api/auth.py  api/workspaces.py  api/cases.py  api/documents.py  api/graph.py  api/query.py
        │ Depends(get_current_user) → Depends(get_case_access(min_role))
  services/   auth_service · access · case_index_registry · index_store · audit
  models/     SQLAlchemy ORM models          core/database.py  async engine + sessions
  pipeline/   UNCHANGED algorithms (+ export_state / load_state)
        │                                   │
   PostgreSQL 18 (graphrag db)        Groq / Ollama (unchanged)
```

### New module boundaries (add these rows to ARCHITECTURE.md §4)

| Module | Responsibility | May depend on | Must NOT |
|---|---|---|---|
| `core/database.py` | Async engine, `async_sessionmaker`, `get_db()` dependency, `session_scope()` for background tasks. **Replaces** the in-memory `documents` dict | `core.config` | contain queries |
| `core/security.py` | Hash and verify passwords; create and decode JWTs; hash refresh tokens | `core.config`, PyJWT, pwdlib | touch the DB |
| `models/*.py` | SQLAlchemy ORM tables (§4) | `core.database` (Base) | import `pipeline` or `schemas` |
| `services/auth_service.py` | Register, login, Google login, refresh rotation, logout | `models`, `core.security`, google-auth | know about HTTP status codes (raise domain errors; the API layer maps them) |
| `services/access.py` | "Can user U do X on case/workspace W?" Role checks | `models` | — |
| `services/case_index_registry.py` | One pipeline bundle (GraphRAG + RAPTOR + HiPPO + QueryEngine) **per case**, lazily loaded from the DB, LRU-cached, with a per-case lock | `pipeline`, `services.index_store` | be bypassed. Routes get pipelines only through it |
| `services/index_store.py` | Save and load a case's pipeline state to and from Postgres | `models`, `pipeline` (only `export_state`/`load_state`) | contain algorithm logic |
| `services/audit.py` | `record(session, action, …)` writes an `audit_events` row | `models` | commit on its own (the caller's transaction does) |
| `pipeline/*` | Unchanged algorithms | as before | **import anything from `models`, `services` or `core.database`** |

Key rule: **the pipeline never knows the database exists.** Persistence goes through
`export_state()` → plain dicts → `index_store` → rows, and back.

---

## 4. Database design

### 4.1 Conventions (best practices, apply to every table)

- **Primary keys:** `UUID`, `server_default=text("gen_random_uuid()")` (built into PG 13+).
  Exceptions: `chunks.id` is `TEXT` (the existing `"{doc_id}_c{idx}"` format), audit rows use
  `BIGINT GENERATED ALWAYS AS IDENTITY`.
- **Timestamps:** `TIMESTAMPTZ` only, never naive. `created_at` defaults to `now()` on the server.
  Mutable tables also get `updated_at` (set with `onupdate=func.now()`).
- **Enums:** `TEXT` + a `CHECK (col IN (...))` constraint, **not** Postgres `ENUM` types.
  Adding a value later is then a one-line migration.
- **Foreign keys:** always declared, always **indexed**. `ON DELETE CASCADE` for children
  (a case owns its documents); `ON DELETE SET NULL` for "who did it" columns (`created_by`,
  `uploaded_by`, `user_id` in logs) so deleting a user doesn't delete evidence.
- **Constraint names:** set a SQLAlchemy `MetaData(naming_convention=…)` (§6 Phase 1) so
  Alembic can generate and drop constraints reliably.
- **Emails:** stored as typed, unique on `lower(email)` through a functional unique index.
- **Secrets are never stored in plain text:** passwords are argon2 hashes; refresh tokens are SHA-256 hashes.
- **Index-derived tables** (entities, relationships, RAPTOR/HiPPO nodes…) have an FK to
  `cases` with cascade, but **no FK to `documents`/`chunks`**. Known limitation §8.2 ("delete
  doesn't un-index") means these rows can legitimately point at deleted documents.

### 4.2 Entity-relationship overview

```
users ─┬─< oauth_accounts
       ├─< refresh_tokens
       └─< workspace_members >── workspaces ──< cases ─┬─< documents ──< chunks
                                                       ├─< entities ──< entity_mentions
                                                       ├─< relationships
                                                       ├─< communities
                                                       ├─< raptor_nodes
                                                       ├─< hippo_nodes
                                                       ├── case_index_meta (1:1)
                                                       ├─< query_logs
                                                       └─< audit_events (also workspace-level)
```

### 4.3 Tables (DDL is the reference; implement as SQLAlchemy models + an Alembic migration)

```sql
-- ── identity ──────────────────────────────────────────────────────────────
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           TEXT NOT NULL,
  full_name       TEXT NOT NULL,
  password_hash   TEXT,                       -- NULL for Google-only accounts
  email_verified  BOOLEAN NOT NULL DEFAULT false,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  last_login_at   TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email));

CREATE TABLE oauth_accounts (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider         TEXT NOT NULL CHECK (provider IN ('google')),
  provider_subject TEXT NOT NULL,             -- Google "sub" claim, stable per Google account
  email            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_subject)
);
CREATE INDEX ix_oauth_accounts_user_id ON oauth_accounts (user_id);

CREATE TABLE refresh_tokens (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash   TEXT NOT NULL UNIQUE,          -- sha256 hex of the raw token
  expires_at   TIMESTAMPTZ NOT NULL,
  revoked_at   TIMESTAMPTZ,
  replaced_by  UUID REFERENCES refresh_tokens(id) ON DELETE SET NULL,
  user_agent   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

-- ── tenancy ───────────────────────────────────────────────────────────────
CREATE TABLE workspaces (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  kind        TEXT NOT NULL CHECK (kind IN ('personal','organization')),
  created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workspace_members (
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX ix_workspace_members_user_id ON workspace_members (user_id);

-- ── investigation ─────────────────────────────────────────────────────────
CREATE TABLE cases (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  reference_code  TEXT,                       -- optional user label, e.g. "INV-2026-014"
  title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  description     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','in_progress','closed','archived')),
  priority        TEXT NOT NULL DEFAULT 'medium'
                  CHECK (priority IN ('low','medium','high','critical')),
  created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  closed_at       TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_cases_workspace_status ON cases (workspace_id, status);
CREATE UNIQUE INDEX uq_cases_workspace_reference
  ON cases (workspace_id, reference_code) WHERE reference_code IS NOT NULL;

CREATE TABLE documents (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id             UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  uploaded_by         UUID REFERENCES users(id) ON DELETE SET NULL,
  filename            TEXT NOT NULL,          -- original name, for display only
  stored_path         TEXT NOT NULL,          -- server path, never sent to clients
  content_type        TEXT NOT NULL DEFAULT '',
  size_bytes          BIGINT NOT NULL CHECK (size_bytes >= 0),
  sha256              CHAR(64) NOT NULL,      -- evidence integrity
  status              TEXT NOT NULL DEFAULT 'uploaded'
                      CHECK (status IN ('uploaded','indexing','indexed','error')),
  error               TEXT,
  chunk_count         INTEGER NOT NULL DEFAULT 0,
  indexing_started_at TIMESTAMPTZ,
  indexed_at          TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_documents_case_id ON documents (case_id);
CREATE INDEX ix_documents_case_sha ON documents (case_id, sha256);

CREATE TABLE chunks (
  id           TEXT PRIMARY KEY,              -- "{doc_id}_c{idx}" exactly as chunk_text makes it
  document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  case_id      UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  chunk_index  INTEGER NOT NULL,
  text         TEXT NOT NULL,
  UNIQUE (document_id, chunk_index)
);
CREATE INDEX ix_chunks_case_id ON chunks (case_id);

-- ── GraphRAG index (derived, per case) ────────────────────────────────────
CREATE TABLE entities (
  id               UUID PRIMARY KEY,          -- keep the pipeline's uuid4
  case_id          UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  seq              INTEGER NOT NULL,          -- dict insertion order (affects tie-breaks and output order)
  name             TEXT NOT NULL,
  normalized_key   TEXT NOT NULL,             -- name.lower(), same key as _name_to_id
  type             TEXT NOT NULL CHECK (type IN ('PERSON','ORGANIZATION','LOCATION','DATE',
                     'EVENT','CONCEPT','PRODUCT','LAW','DISEASE','DRUG','OTHER')),
  description      TEXT NOT NULL DEFAULT '',
  community        INTEGER NOT NULL DEFAULT -1,
  UNIQUE (case_id, normalized_key),
  UNIQUE (case_id, seq)
);

CREATE TABLE entity_mentions (               -- replaces Entity.source_chunks
  entity_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  chunk_id   TEXT NOT NULL,                  -- no FK on purpose (see 4.1)
  position   INTEGER NOT NULL,               -- keeps list order
  PRIMARY KEY (entity_id, chunk_id)
);

CREATE TABLE relationships (
  id                UUID PRIMARY KEY,
  case_id           UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  seq               INTEGER NOT NULL,        -- original list order; needed to rebuild the graph exactly
  source_entity_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  target_entity_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation_type     TEXT NOT NULL,
  description       TEXT NOT NULL DEFAULT '',
  weight            REAL NOT NULL DEFAULT 1.0,
  UNIQUE (case_id, seq)
);
CREATE INDEX ix_relationships_source ON relationships (source_entity_id);
CREATE INDEX ix_relationships_target ON relationships (target_entity_id);

CREATE TABLE communities (
  case_id       UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  community_id  INTEGER NOT NULL,
  summary       TEXT NOT NULL,
  PRIMARY KEY (case_id, community_id)
);

-- ── RAPTOR / HiPPO indexes (derived, per case) ────────────────────────────
CREATE TABLE raptor_nodes (
  id         UUID PRIMARY KEY,
  case_id    UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,               -- dict insertion order
  level      SMALLINT NOT NULL,
  text       TEXT NOT NULL,
  parent_id  UUID,                           -- no self-FK: rows are bulk-replaced
  children   UUID[] NOT NULL DEFAULT '{}',
  doc_ids    TEXT[] NOT NULL DEFAULT '{}',
  embedding  REAL[] NOT NULL,                -- 768 floats
  UNIQUE (case_id, seq)
);

CREATE TABLE hippo_nodes (
  id         UUID PRIMARY KEY,
  case_id    UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  level      SMALLINT NOT NULL,
  text       TEXT NOT NULL,
  parent_id  UUID,
  child_ids  UUID[] NOT NULL DEFAULT '{}',
  doc_id     TEXT NOT NULL DEFAULT '',
  chunk_idx  INTEGER NOT NULL DEFAULT 0,
  embedding  REAL[] NOT NULL,
  UNIQUE (case_id, seq)
);

CREATE TABLE case_index_meta (               -- small pieces of state that aren't rows
  case_id          UUID PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
  raptor_root_ids  JSONB NOT NULL DEFAULT '[]',
  hippo_levels     JSONB NOT NULL DEFAULT '{}', -- {"0": [ids…], "1": [...]}, exactly as in memory
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── activity ──────────────────────────────────────────────────────────────
CREATE TABLE query_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id     UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
  question    TEXT NOT NULL,
  method      TEXT NOT NULL CHECK (method IN ('graphrag','raptor','hippo','hybrid')),
  top_k       SMALLINT NOT NULL,
  answer      TEXT NOT NULL,
  reasoning   TEXT NOT NULL DEFAULT '',
  confidence  REAL NOT NULL,
  entities    JSONB NOT NULL DEFAULT '[]',
  sources     JSONB NOT NULL DEFAULT '[]',
  latency_ms  INTEGER NOT NULL,              -- needed for the future baseline comparison
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_query_logs_case_created ON query_logs (case_id, created_at DESC);

CREATE TABLE audit_events (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id  UUID REFERENCES workspaces(id) ON DELETE SET NULL,
  case_id       UUID REFERENCES cases(id) ON DELETE SET NULL,
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
  action        TEXT NOT NULL,               -- e.g. 'case.create', 'document.upload'
  target_type   TEXT,
  target_id     TEXT,
  details       JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_case_created ON audit_events (case_id, created_at DESC);
CREATE INDEX ix_audit_workspace_created ON audit_events (workspace_id, created_at DESC);
```

**Why `seq` columns:** Python dicts and lists keep insertion order, and some pipeline output
depends on it (graph edge `relation` = the first relationship seen; `weight` = the count).
`seq` lets `load_state` rebuild the exact same in-memory objects.

**Audit actions to record** (strings, keep this list in `services/audit.py` as constants):
`auth.register`, `auth.login`, `auth.google_login`, `workspace.create`, `workspace.member_add`,
`workspace.member_role_change`, `workspace.member_remove`, `case.create`, `case.update`,
`case.delete`, `document.upload`, `document.indexed`, `document.index_failed`,
`document.delete`, `query.run`.

---

## 5. HTTP API (new contract)

All paths are prefixed with `/api`. "Auth" = requires `Authorization: Bearer <access_token>`.
Missing or invalid token → **401** with header `WWW-Authenticate: Bearer`.
A user who is **not a member** of the workspace that owns the case → **404** (don't reveal
that it exists). A member with **too low a role** → **403**.

### 5.1 Role matrix

| Action | viewer | member | admin | owner |
|---|:-:|:-:|:-:|:-:|
| See workspace, cases, documents, graph; run queries; see query history | ✓ | ✓ | ✓ | ✓ |
| Create case, edit case, upload documents | | ✓ | ✓ | ✓ |
| Delete documents, delete cases, see audit log, add/remove members, change roles (not the owner's) | | | ✓ | ✓ |
| Rename/delete workspace, make another member owner | | | | ✓ |

A personal workspace always has exactly one member (its owner). Adding members there → **400**.

### 5.2 Endpoints

| Method & path | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `GET /health` | no | — | unchanged | — |
| `GET /auth/config` | no | — | `{google_client_id: string \| null}` | — |
| `POST /auth/register` | no | `RegisterRequest` | **201** `TokenResponse` + sets refresh cookie | 409 email taken; 422 |
| `POST /auth/login` | no | `LoginRequest` | `TokenResponse` + cookie | 401 "Invalid email or password." (same message for every failure) |
| `POST /auth/google` | no | `{credential: string}` | `TokenResponse` + cookie | 401 bad token / unverified email; 503 Google login not configured |
| `POST /auth/refresh` | cookie | — | `TokenResponse` + new cookie | 401 |
| `POST /auth/logout` | cookie | — | **204**, cookie cleared | — |
| `GET /auth/me` | yes | — | `UserOut` | — |
| `GET /workspaces` | yes | — | `WorkspaceOut[]` (includes `my_role`) | — |
| `POST /workspaces` | yes | `{name}` | **201** `WorkspaceOut` (kind = organization, caller = owner) | — |
| `PATCH /workspaces/{id}` | owner | `{name}` | `WorkspaceOut` | 404/403 |
| `DELETE /workspaces/{id}` | owner | — | **204** | 400 personal workspace |
| `GET /workspaces/{id}/members` | yes | — | `MemberOut[]` | 404 |
| `POST /workspaces/{id}/members` | admin | `{email, role}` (user must already be registered) | **201** `MemberOut` | 404 no such user; 409 already a member; 400 personal |
| `PATCH /workspaces/{id}/members/{user_id}` | admin | `{role}` | `MemberOut` | 403 changing the owner |
| `DELETE /workspaces/{id}/members/{user_id}` | admin (or self, to leave) | — | **204** | 400 removing the last owner |
| `GET /workspaces/{id}/cases` | yes | query `?status=` optional | `CaseOut[]` (newest first) | 404 |
| `POST /workspaces/{id}/cases` | member | `CaseCreate` | **201** `CaseOut` | 409 duplicate reference_code |
| `GET /cases/{case_id}` | yes | — | `CaseOut` (+ counts) | 404 |
| `PATCH /cases/{case_id}` | member | `CaseUpdate` | `CaseOut` | 404/403 |
| `DELETE /cases/{case_id}` | admin | — | **204** (also deletes files on disk + cache entry) | 404/403 |
| `GET /cases/{case_id}/audit` | admin | `?limit=50` | `AuditEventOut[]` | |
| `POST /cases/{case_id}/documents/upload` | member | multipart `file` | `DocRecord` | 413; 400 case closed/archived |
| `GET /cases/{case_id}/documents` | yes | — | `DocRecord[]` | |
| `GET /cases/{case_id}/documents/{doc_id}` | yes | — | `DocRecord` | 404 |
| `DELETE /cases/{case_id}/documents/{doc_id}` | admin | — | `{deleted: doc_id}` | 404 |
| `GET /cases/{case_id}/graph` | yes | — | `GraphData` (unchanged shape) | |
| `GET /cases/{case_id}/graph/stats` | yes | — | `GraphStats` (unchanged shape) | |
| `GET /cases/{case_id}/graph/entity/{entity_id}` | yes | — | `EntityDetail` (unchanged) | 404 |
| `POST /cases/{case_id}/query` | yes | `QueryRequest` (unchanged) | `QueryResponse` (unchanged) | 400 no documents in case |
| `GET /cases/{case_id}/query/suggestions` | yes | — | `{suggestions}` | |
| `GET /cases/{case_id}/queries` | yes | `?limit=50` | `QueryLogOut[]` (newest first) | |

**Removed:** `/documents/*`, `/graph/*`, `/query`, `/query/suggestions` (the global versions).

### 5.3 Schemas (Pydantic, `backend/schemas/`)

```python
# schemas/auth.py
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GoogleLoginRequest(BaseModel):
    credential: str            # the ID token from Google Identity Services

class UserOut(BaseModel):
    id: str; email: str; full_name: str; email_verified: bool
    has_password: bool         # False for Google-only accounts
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int            # seconds
    user: UserOut

# schemas/workspaces.py
Role = Literal["owner", "admin", "member", "viewer"]
class WorkspaceCreate(BaseModel): name: str = Field(min_length=1, max_length=120)
class WorkspaceOut(BaseModel):
    id: str; name: str; kind: Literal["personal", "organization"]
    my_role: Role; created_at: datetime
class MemberAdd(BaseModel): email: EmailStr; role: Role = "member"  # "owner" rejected → 400
class MemberRoleUpdate(BaseModel): role: Role
class MemberOut(BaseModel): user_id: str; email: str; full_name: str; role: Role; created_at: datetime

# schemas/cases.py
CaseStatus = Literal["open", "in_progress", "closed", "archived"]
CasePriority = Literal["low", "medium", "high", "critical"]
class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    reference_code: Optional[str] = Field(default=None, max_length=50)
    priority: CasePriority = "medium"
class CaseUpdate(BaseModel):   # all optional; only the sent fields change
    title: Optional[str] = None; description: Optional[str] = None
    reference_code: Optional[str] = None
    status: Optional[CaseStatus] = None; priority: Optional[CasePriority] = None
class CaseOut(BaseModel):
    id: str; workspace_id: str; reference_code: Optional[str]; title: str; description: str
    status: CaseStatus; priority: CasePriority
    created_by: Optional[str]; created_at: datetime; updated_at: datetime; closed_at: Optional[datetime]
    document_count: int = 0

# schemas/documents.py — DocRecord: existing fields kept; NEW fields added with defaults
class DocRecord(BaseModel):
    id: str; filename: str; content_type: str; size_bytes: int
    status: Literal["uploaded", "indexing", "indexed", "error"] = "uploaded"
    error: Optional[str] = None
    chunks: int = 0                 # maps from documents.chunk_count
    case_id: str = ""               # NEW
    sha256: str = ""                # NEW
    uploaded_by: Optional[str] = None  # NEW
    created_at: Optional[datetime] = None  # NEW

# schemas/query.py — QueryRequest/QueryResponse unchanged; add:
class QueryLogOut(BaseModel):
    id: str; question: str; method: str; answer: str; confidence: float
    entities: List[Dict]; sources: List[str]; latency_ms: int
    user_id: Optional[str]; created_at: datetime

# schemas/audit.py
class AuditEventOut(BaseModel):
    id: int; action: str; user_id: Optional[str]; target_type: Optional[str]
    target_id: Optional[str]; details: Dict; created_at: datetime
```

Use `model_config = ConfigDict(from_attributes=True)` where a schema is built from an ORM
object. UUIDs go out as `str` (convert with `str(row.id)`).

---

## 6. Implementation phases

### Phase 0: Setup (owner + agent)

1. Owner approves §2 (decisions, dependencies, interface changes).
2. **Postgres** (already installed: `C:\Program Files\PostgreSQL\18`). Create a DB user and
   database (the owner runs this; the agent must not see the password):
   ```powershell
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE ROLE graphrag LOGIN PASSWORD '<choose>';"
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE graphrag OWNER graphrag;"
   ```
3. **Google OAuth client** (owner, in Google Cloud Console → APIs & Services → Credentials):
   create an **OAuth client ID** of type *Web application*. Under **Authorized JavaScript
   origins** add `http://localhost:4200` **and** `http://localhost`. No redirect URI is
   needed. Configure the OAuth consent screen (External, testing mode, add your test users).
   Copy the **Client ID** (not a secret).
4. Owner adds to `.env` (never commit; the agent must never read it):
   ```
   DATABASE_URL=postgresql+asyncpg://graphrag:<password>@localhost:5432/graphrag
   JWT_SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_urlsafe(64))">
   GOOGLE_CLIENT_ID=<...>.apps.googleusercontent.com
   ```
5. Agent updates `.env.example` with these keys and placeholder values, plus:
   ```
   JWT_ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=15
   REFRESH_TOKEN_EXPIRE_DAYS=7
   COOKIE_SECURE=false            # true when served over HTTPS
   INDEX_CACHE_MAX_CASES=4        # how many cases' indexes are kept in RAM
   ```
   Also change `UPLOAD_DIR=./uploads` → `UPLOAD_DIR=./data/uploads` in `.env.example` to match
   CLAUDE.md (the owner updates `.env` themselves).

**Done when:** `psql -U graphrag -d graphrag -c "select 1"` works, and the owner confirms `.env` is filled in.

---

### Phase 1: Database foundation

**Files:** `requirements.txt`, `backend/core/config.py`, `backend/core/database.py` (rewrite),
`backend/models/*.py` (new), `alembic.ini` + `alembic/` (new), `backend/app/main.py`.

1. Add the approved packages to `requirements.txt`; `pip install -r requirements.txt`.
2. `config.py`: add settings fields:
   ```python
   DATABASE_URL: str = "postgresql+asyncpg://graphrag:graphrag@localhost:5432/graphrag"
   JWT_SECRET_KEY: str = ""              # empty → startup fails loudly (see step 6)
   JWT_ALGORITHM: str = "HS256"
   ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
   REFRESH_TOKEN_EXPIRE_DAYS: int = 7
   COOKIE_SECURE: bool = False
   GOOGLE_CLIENT_ID: str = ""            # empty → Google login disabled (503)
   INDEX_CACHE_MAX_CASES: int = 4
   ```
3. Rewrite `core/database.py` (the in-memory dict goes away; every importer of `documents` is
   rewritten in Phases 3–4):
   ```python
   from contextlib import asynccontextmanager
   from typing import AsyncIterator
   from sqlalchemy import MetaData
   from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
   from sqlalchemy.orm import DeclarativeBase
   from backend.core.config import settings

   # ── naming convention: stable constraint names for Alembic ─────────────────
   NAMING_CONVENTION = {
       "ix": "ix_%(table_name)s_%(column_0_N_name)s",
       "uq": "uq_%(table_name)s_%(column_0_N_name)s",
       "ck": "ck_%(table_name)s_%(constraint_name)s",
       "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
       "pk": "pk_%(table_name)s",
   }

   class Base(DeclarativeBase):
       metadata = MetaData(naming_convention=NAMING_CONVENTION)

   engine = create_async_engine(settings.DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
   SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

   async def get_db() -> AsyncIterator[AsyncSession]:
       """FastAPI dependency: one session per request, committed by the route."""
       async with SessionLocal() as session:
           yield session

   @asynccontextmanager
   async def session_scope() -> AsyncIterator[AsyncSession]:
       """For background tasks: commits on success, rolls back on error."""
       async with SessionLocal() as session:
           try:
               yield session
               await session.commit()
           except Exception:
               await session.rollback()
               raise
   ```
   Rule: **routes call `await db.commit()` explicitly** after writes. Background tasks never
   reuse the request's session; they open `session_scope()`.
4. Models in `backend/models/` (the empty placeholder package): one file per area:
   `user.py` (User, OAuthAccount, RefreshToken), `workspace.py` (Workspace, WorkspaceMember),
   `case.py` (Case, Document, Chunk), `index.py` (EntityRow, EntityMention, RelationshipRow,
   CommunityRow, RaptorNodeRow, HippoNodeRow, CaseIndexMeta), `activity.py` (QueryLog,
   AuditEvent). `models/__init__.py` imports all of them, so `Base.metadata` sees every table.
   - Name ORM classes `…Row` where they clash with pipeline dataclasses (`Entity`,
     `Relationship`), to avoid confusion.
   - Use SQLAlchemy 2.0 typed style: `id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))`.
   - Arrays: `from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID`; `ARRAY(REAL)`, `ARRAY(UUID(as_uuid=True))`, `ARRAY(Text)`.
   - CHECK constraints via `__table_args__ = (CheckConstraint("status IN (...)", name="status"), ...)`.
   - Functional unique index: `Index("uq_users_email_lower", func.lower(User.email), unique=True)`.
   - Partial unique index: `Index(..., postgresql_where=text("reference_code IS NOT NULL"), unique=True)`.
   - Do **not** add `relationship()` back-references unless a query needs them. Explicit queries are easier to follow.
5. Alembic:
   ```powershell
   alembic init -t async alembic
   ```
   - In `alembic/env.py`: import `backend.models` (for side effects) and set
     `target_metadata = Base.metadata`; set `config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)`
     so the URL comes from `.env`, not `alembic.ini`.
   - Leave `sqlalchemy.url` in `alembic.ini` empty (no passwords in files).
   - `alembic revision --autogenerate -m "initial schema"`. **Read the generated file**:
     autogenerate misses functional and partial indexes and server defaults sometimes. Fix it
     against §4.3 by hand.
   - `alembic upgrade head`.
6. `main.py`: add a FastAPI **lifespan** that (a) refuses to start if `JWT_SECRET_KEY` is empty
   (`raise RuntimeError("JWT_SECRET_KEY is not set")`), (b) runs `SELECT 1` and logs
   `logger.info("Database connected")`, (c) on shutdown `await engine.dispose()`. Startup
   recovery for interrupted indexing is added in Phase 4.

**Done when:**
- `python -c "import backend.app.main"` passes.
- `alembic upgrade head` succeeds; `psql -U graphrag -d graphrag -c "\dt"` lists all 17 tables from §4.3 plus `alembic_version`.
- `alembic downgrade base` then `alembic upgrade head` both succeed (the migration is reversible).
- uvicorn starts and `curl.exe http://localhost:8000/api/health` still works.

---

### Phase 2: Authentication backend

**Files:** `backend/core/security.py`, `backend/services/__init__.py`,
`backend/services/auth_service.py`, `backend/services/audit.py`, `backend/schemas/auth.py`,
`backend/api/auth.py`, `backend/api/router.py`, `backend/app/dependencies.py`.

1. `core/security.py`:
   ```python
   _hasher = PasswordHash.recommended()        # pwdlib, argon2

   def hash_password(pw: str) -> str
   def verify_password(pw: str, hashed: str) -> bool
   def create_access_token(user_id: str) -> Tuple[str, int]
       # claims: sub=user_id, type="access", iat, exp=now+ACCESS_TOKEN_EXPIRE_MINUTES
       # returns (token, expires_in_seconds)
   def decode_access_token(token: str) -> str
       # jwt.decode(..., algorithms=[settings.JWT_ALGORITHM]); check type == "access"
       # returns user_id; raises jwt.PyJWTError on anything wrong
   def new_refresh_token() -> str              # secrets.token_urlsafe(48)
   def hash_refresh_token(raw: str) -> str     # hashlib.sha256(raw.encode()).hexdigest()
   ```
   Always pass `algorithms=[...]` to `jwt.decode` (prevents algorithm-confusion attacks).
2. `services/auth_service.py`: plain async functions that take `session: AsyncSession`.
   Define `class AuthError(Exception)` and `class EmailTakenError(Exception)`; the API layer
   maps them to 401/409.
   - `register(session, email, password, full_name) -> User`: check the lower-cased email
     doesn't exist → create the User → `_create_personal_workspace(session, user)` (Workspace
     kind=personal, name=f"{full_name}'s workspace", member role=owner) → audit `auth.register`.
     `flush()`, don't commit (the route commits).
   - `authenticate(session, email, password) -> User`: user must exist, be active and have a
     `password_hash`, and `verify_password` must pass; otherwise raise `AuthError` with the
     **same message** in every case. Update `last_login_at`.
   - `login_with_google(session, credential) -> User`:
     ```python
     if not settings.GOOGLE_CLIENT_ID: raise GoogleNotConfiguredError
     info = await asyncio.to_thread(
         id_token.verify_oauth2_token, credential, google_requests.Request(), settings.GOOGLE_CLIENT_ID
     )   # verifies signature, expiry, audience, issuer
     if not info.get("email_verified"): raise AuthError
     # 1) OAuthAccount(provider='google', provider_subject=info['sub']) exists → its user
     # 2) else a User with lower(email) == info['email'].lower() exists → link: add OAuthAccount, set email_verified=True
     # 3) else create User(password_hash=None, email_verified=True, full_name=info.get('name') or email)
     #    + personal workspace + OAuthAccount
     ```
     Wrap `verify_oauth2_token` errors (`ValueError`) into `AuthError`, logging at `warning`.
   - `issue_refresh_token(session, user, user_agent) -> str`: create the raw token, store its
     hash with `expires_at`, return the raw token.
   - `rotate_refresh_token(session, raw, user_agent) -> Tuple[User, str]`: look up by hash.
     - not found or expired → `AuthError`
     - **already revoked → token reuse**: revoke **all** of that user's tokens, log a
       warning, raise `AuthError`
     - otherwise revoke it, create a new one, set `replaced_by`, return (user, new_raw)
   - `revoke_refresh_token(session, raw) -> None`
3. `api/auth.py`: routes from §5.2. A helper sets the cookie in one place:
   ```python
   REFRESH_COOKIE = "refresh_token"

   def _set_refresh_cookie(response: Response, raw: str) -> None:
       response.set_cookie(
           REFRESH_COOKIE, raw,
           max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
           httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
           path="/api/auth",            # the browser only sends it to auth endpoints
       )
   ```
   Every successful register/login/google/refresh: create the access token + refresh token,
   set the cookie, `await db.commit()`, return `TokenResponse`. Logout: revoke, then
   `response.delete_cookie(REFRESH_COOKIE, path="/api/auth")`, return 204.
   Read the cookie with `refresh_token: Optional[str] = Cookie(default=None)`.
   Log `logger.info(f"User logged in: {user.id}")`. **Never log passwords, tokens or the Google credential.**
4. `app/dependencies.py`: add the current-user dependency.
   ```python
   _bearer = HTTPBearer(auto_error=False)      # also adds the "Authorize" button in /docs

   async def get_current_user(
       creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
       db: AsyncSession = Depends(get_db),
   ) -> User:
       if creds is None: raise _unauthorized()
       try:
           user_id = decode_access_token(creds.credentials)
       except jwt.PyJWTError:
           raise _unauthorized()
       user = await db.get(User, uuid.UUID(user_id))
       if user is None or not user.is_active: raise _unauthorized()
       return user

   def _unauthorized() -> HTTPException:
       return HTTPException(401, "Not authenticated.", headers={"WWW-Authenticate": "Bearer"})
   ```
5. `api/router.py`: `include_router(auth.router)`.
6. CORS: `allow_credentials=True` is already set. Check that `.env` has explicit origins, not
   `*` (browsers reject `*` together with credentials). Change the `config.py` default for
   `CORS_ORIGINS` from `"*"` to `"http://localhost:4200"`.

**Done when** (uvicorn running):
```powershell
curl.exe -i -c jar.txt -H "Content-Type: application/json" -d '{\"email\":\"a@test.com\",\"password\":\"password123\",\"full_name\":\"A\"}' http://localhost:8000/api/auth/register   # 201 + Set-Cookie
curl.exe -i -H "Content-Type: application/json" -d '{\"email\":\"a@test.com\",\"password\":\"wrong\"}' http://localhost:8000/api/auth/login       # 401
curl.exe -i -b jar.txt -c jar.txt -X POST http://localhost:8000/api/auth/refresh    # 200, new cookie
curl.exe -i -b jar.txt -X POST http://localhost:8000/api/auth/refresh               # replay the OLD jar → 401 (reuse detection)
curl.exe -i -H "Authorization: Bearer <token>" http://localhost:8000/api/auth/me     # 200
curl.exe -i http://localhost:8000/api/auth/me                                        # 401
```
Also check in psql: `users` has one row, `password_hash` starts with `$argon2`, a personal
workspace and owner membership exist, and `refresh_tokens.token_hash` is 64 hex chars.
(Google login is tested from the browser in Phase 5.) Delete `jar.txt` afterwards.

---

### Phase 3: Workspaces, cases and access control

**Files:** `backend/services/access.py`, `backend/schemas/workspaces.py`,
`backend/schemas/cases.py`, `backend/schemas/audit.py`, `backend/api/workspaces.py`,
`backend/api/cases.py`, `backend/api/router.py`, `backend/app/dependencies.py`.

1. `services/access.py`:
   ```python
   ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}

   async def get_membership(session, workspace_id, user_id) -> Optional[WorkspaceMember]
   def has_role(role: str, min_role: str) -> bool: return ROLE_RANK[role] >= ROLE_RANK[min_role]
   ```
2. Dependency **factories** in `app/dependencies.py` (these are what every protected route uses):
   ```python
   @dataclass
   class CaseAccess:
       case: Case
       role: str
       user: User

   def require_case_role(min_role: str):
       async def dep(case_id: uuid.UUID, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)) -> CaseAccess:
           case = await db.get(Case, case_id)
           member = case and await get_membership(db, case.workspace_id, user.id)
           if not case or not member:
               raise HTTPException(404, "Case not found.")
           if not has_role(member.role, min_role):
               raise HTTPException(403, "Insufficient role.")
           return CaseAccess(case, member.role, user)
       return dep

   def require_workspace_role(min_role: str): ...   # same pattern → WorkspaceAccess
   ```
   Usage: `access: CaseAccess = Depends(require_case_role("member"))`.
   Typing `case_id: uuid.UUID` makes FastAPI return 422 for malformed IDs automatically.
3. `api/workspaces.py` and `api/cases.py`: implement §5.2 exactly. Notes:
   - List workspaces = join `workspace_members` on the current user.
   - Case `status` → `closed` sets `closed_at = now()`; reopening clears it.
   - Duplicate `reference_code` → catch `sqlalchemy.exc.IntegrityError`, roll back, return 409.
   - `DELETE /cases/{id}`: delete the row (cascades everything), then
     `shutil.rmtree(UPLOAD_DIR / str(case_id), ignore_errors=True)` in `asyncio.to_thread`,
     then `case_index_registry.evict(case_id)` (Phase 4; add a TODO until then).
   - `document_count` in `CaseOut`: one `SELECT case_id, count(*) … GROUP BY case_id` for list
     endpoints. Don't run N+1 queries.
   - Write an audit event for every mutation (§4.3 list).

**Done when:** with two registered users A and B:
- A creates org workspace W and case C → 201s.
- B `GET /api/cases/C` → **404**. A adds B as `viewer` → B `GET` → 200; B `PATCH` → **403**.
- A changes B to `member` → B `PATCH` → 200.
- Adding a member to A's personal workspace → 400.
- `audit_events` has rows for each mutation.

---

### Phase 4: Per-case pipelines + persistence (the core change)

**Files:** `backend/pipeline/graphrag_indexer.py`, `raptor_runner.py`, `hippo_retriever.py`,
`query_engine.py` (each **adds** two methods only), `backend/services/index_store.py`,
`backend/services/case_index_registry.py`, `backend/app/dependencies.py`,
`backend/api/documents.py`, `backend/api/graph.py`, `backend/api/query.py`,
`backend/app/main.py`, `backend/schemas/documents.py`, `backend/schemas/query.py`.

#### 4a. `export_state` / `load_state` on each pipeline class

Plain dicts and lists of primitives only (no ORM objects). Put them in a new section
`# ── state (persistence) ───…` at the bottom of each class. **Do not change any existing method.**

```python
# GraphRAGIndexer
def export_state(self) -> Dict:
    return {
        "entities": [asdict(e) for e in self.entities.values()],          # dict order preserved
        "relationships": [asdict(r) for r in self.relationships],         # list order = seq
        "communities": dict(self.community_summaries),
        "node_community": {n: d.get("community", -1) for n, d in self.graph.nodes(data=True)},
    }

def load_state(self, state: Dict) -> None:
    self.graph = nx.Graph(); self.entities = {}; self.relationships = []
    self._name_to_id = {}
    for e in state["entities"]:
        ent = Entity(**e)
        self.entities[ent.id] = ent
        self._name_to_id[ent.name.lower()] = ent.id
        self.graph.add_node(ent.id, name=ent.name, type=ent.type,
                            community=state["node_community"].get(ent.id, -1))
    # replay in original order → same weights and same "first relation" as index_document
    for r in state["relationships"]:
        rel = Relationship(**r); self.relationships.append(rel)
        if self.graph.has_edge(rel.source_id, rel.target_id):
            self.graph[rel.source_id][rel.target_id]["weight"] += 1
        else:
            self.graph.add_edge(rel.source_id, rel.target_id, weight=1, relation=rel.relation_type)
    self.community_summaries = {int(k): v for k, v in state["communities"].items()}
```
- `_name_to_id` key: `index_document` stores `_normalise(name).lower()`, and `Entity.name` is
  already the normalised name, so `ent.name.lower()` matches. Check this when implementing.
- A node gets `community` only after Louvain has run (graphs with ≥ 3 nodes). Setting `-1` on
  load is equivalent, because every reader uses `.get("community", -1)`.

```python
# RaptorRunner
def export_state(self) -> Dict:  {"nodes": [asdict(n) for n in self.nodes.values()], "root_ids": list(self.root_ids)}
def load_state(self, state: Dict) -> None   # rebuild self.nodes in list order; self.root_ids

# HippoRetriever
def export_state(self) -> Dict:  {"nodes": [asdict(n) …], "levels": {str(k): v for k, v in self.levels.items()}}
def load_state(self, state: Dict) -> None   # levels keys back to int

# QueryEngine (history only; the retrievers are saved separately)
def export_state(self) -> Dict: {"history": list(self.history)}
def load_state(self, state: Dict) -> None
```

**Self-check (run once, then delete the script):** a scratch script that indexes
`dhaka.txt` into fresh instances, calls `export_state`, loads into new instances, and asserts
`get_graph_data()`, `get_stats()` and `retrieve(...)` results are identical. Put the script in
the scratchpad, not in the repo.

#### 4b. `services/index_store.py`

```python
async def load_case_state(session, case_id) -> Optional[Dict]
    # SELECT each derived table WHERE case_id = :id ORDER BY seq  (entity_mentions ORDER BY position)
    # Returns {"graphrag": {...}, "raptor": {...}, "hippo": {...}, "engine": {"history": [...]}}
    # engine.history = last 3 query_logs for the case, oldest first: [{"question", "answer"}]
    # Returns None when the case has no index rows yet.

async def save_case_state(session, case_id, graphrag, raptor, hippo) -> None
    # "Replace snapshot": within the caller's transaction
    #   DELETE FROM entities/relationships/communities/raptor_nodes/hippo_nodes WHERE case_id=:id
    #   (entity_mentions go via cascade)
    #   bulk INSERT the rows from export_state()  →  session.execute(insert(Model), list_of_dicts)
    #   UPSERT case_index_meta (insert … on_conflict_do_update)
```
- Map `Entity.source_chunks` (a list) ↔ `entity_mentions` rows with `position = index`.
- `doc_ids` and `doc_id` are strings in the pipeline. Store them as `TEXT`, not `UUID`; the eval
  CLI may use non-UUID doc ids.
- Convert embeddings to `list[float]` before inserting (NumPy floats are rejected by asyncpg).
- **Why snapshot-replace instead of diffing:** Louvain re-runs over the whole case graph
  after every document, so communities and entity rows change globally anyway. Replacing is
  simple and always consistent. Cost: rewriting a case's index on each upload, which is fine
  at demo scale. Record this as a known limitation in ARCHITECTURE.md §8.

#### 4c. `services/case_index_registry.py`: replaces the global singletons (supersedes D2)

```python
@dataclass
class CaseBundle:
    graphrag: GraphRAGIndexer
    raptor: RaptorRunner
    hippo: HippoRetriever
    engine: QueryEngine
    lock: asyncio.Lock          # serialises indexing within one case

class CaseIndexRegistry:
    def __init__(self, max_cases: int = settings.INDEX_CACHE_MAX_CASES):
        self._cache: "OrderedDict[uuid.UUID, CaseBundle]" = OrderedDict()   # LRU
        self._load_lock = asyncio.Lock()

    async def get(self, case_id) -> CaseBundle:
        # hit → move_to_end, return
        # miss → under _load_lock: re-check, open session_scope(), load_case_state,
        #   build new instances (same constructor defaults as today), load_state if not None,
        #   insert, evict the oldest if over max_cases (never evict a bundle whose lock is held)
    def evict(self, case_id) -> None

case_index_registry = CaseIndexRegistry()     # module singleton, exposed via get_case_index_registry()
```
`dependencies.py`: delete the global `graphrag/raptor/hippo/query_engine` singletons and their
`get_*` providers. Add `get_case_index_registry()`.

#### 4d. Rewrite `api/documents.py` (case-scoped)

- Router prefix `/cases/{case_id}/documents`.
- Upload (`require_case_role("member")`):
  1. reject if `case.status in ("closed", "archived")` → 400
  2. `doc_id = uuid.uuid4()`; **sanitise the filename**: `safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "file").name)[:100]`
  3. path `UPLOAD_DIR / str(case_id) / f"{doc_id}_{safe}"` (mkdir parents)
  4. stream to disk as today (256 KB, 413 limit) **while** updating `hashlib.sha256()`
  5. insert the `Document` row, audit `document.upload`, `await db.commit()`
  6. `background_tasks.add_task(_run_indexing, case_id, doc_id, path, content_type)`, return `DocRecord`
- `_run_indexing(case_id, doc_id, path, content_type)` **opens its own sessions**:
  ```python
  bundle = await registry.get(case_id)
  async with bundle.lock:
      await _set_status(doc_id, "indexing", indexing_started_at=now)      # own short session
      try:
          text = await extract_text(path, content_type)
          if not text.strip(): raise ValueError("No text could be extracted from the document.")
          chunks = bundle.graphrag.chunk_text(text, str(doc_id))
          result = await bundle.graphrag.index_document(str(doc_id), text, chunks)
          await asyncio.gather(bundle.raptor.build_tree(chunks),
                               bundle.hippo.index_passages(chunks), return_exceptions=True)
          async with session_scope() as s:
              # insert chunk rows, save_case_state(...), set status indexed / chunk_count / indexed_at,
              # audit document.indexed — ONE transaction
      except Exception as exc:
          logger.error(..., exc_info=True)
          # own session: status error + error=str(exc), audit document.index_failed
          registry.evict(case_id)   # the in-memory state may be half-updated; reload from DB next time
  ```
  Keep D3, D4, D5 and D6 exactly as today: same chunks for all three, GraphRAG first, then
  RAPTOR + HiPPO in parallel.
- List / get / delete: query by `case_id` **and** `doc_id` (never by `doc_id` alone, or a user
  could read another case's document by guessing). Delete (`admin`): delete the row and the
  file, audit. It still **does not un-index** (§8.2 stays a known limitation; don't fix it here).

#### 4e. Rewrite `api/graph.py` and `api/query.py` (case-scoped)

- Prefixes `/cases/{case_id}/graph` and `/cases/{case_id}`; role `viewer`.
- Get the bundle with `await registry.get(case_id)` and call the **same** pipeline methods as today.
- `indexed_documents` in stats: `SELECT count(*) FROM documents WHERE case_id=:id AND status='indexed'`.
- `POST /cases/{case_id}/query`:
  ```python
  if no documents in this case: 400 "No documents indexed yet."     # same semantics as today (§8.7)
  t0 = time.perf_counter()
  result = await bundle.engine.query(req.question, req.method, req.top_k)
  latency_ms = int((time.perf_counter() - t0) * 1000)
  # insert QueryLog(user_id=access.user.id, latency_ms=…, …), audit query.run, commit
  return QueryResponse(**result)
  ```
  `QueryEngine.history` is now **per case** (shared by that case's members) and is seeded from
  the last 3 `query_logs` on load. Record this in ARCHITECTURE.md §8 in place of item 6.
- `GET /cases/{case_id}/queries`: newest-first `QueryLogOut[]`.

#### 4f. Startup recovery (in the `main.py` lifespan)

On startup:
`UPDATE documents SET status='error', error='Interrupted by server restart. Please re-upload.' WHERE status IN ('uploaded','indexing')`.
Log the count at `warning`. Without this, documents stay "indexing" forever.

**Done when:**
1. `python -c "import backend.app.main"` passes.
2. As user A, create case C1. Upload `data/input/sample_cases/dhaka.txt` → it reaches `indexed`.
   Run one query per method (`graphrag`, `raptor`, `hippo`, `hybrid`) → sensible answers;
   `query_logs` has 4 rows with `latency_ms > 0`.
3. Create case C2 and upload `cybersecurity_case_01_data_breach.txt`. `GET /cases/C1/graph` contains
   **no** entities from the cybersecurity file (isolation).
4. **Restart uvicorn.** `GET /cases/C1/graph` and `/graph/stats` return the same numbers as
   before the restart, and a query still works (persistence).
5. User B (not a member) gets 404 on every C1 endpoint.
6. Upload a file named `..\..\evil.txt` → it's stored inside `data/uploads/<case_id>/`.

---

### Phase 5: Frontend authentication

**Files:** `frontend/src/index.html`, `app.config.ts`, `app.routes.ts`,
`services/api.service.ts`, `services/auth.service.ts` (new), `auth/auth.interceptor.ts` (new),
`auth/auth.guards.ts` (new), `pages/login.page.ts` (new), `pages/register.page.ts` (new),
`components/google-signin-button.component.ts` (new).
Follow the frontend rules in CLAUDE.md: standalone components, inline template and styles,
signals, `@if`/`@for`, colours from CSS variables, no `any` without a comment.

1. `index.html`: add `<script src="https://accounts.google.com/gsi/client" async defer></script>` in `<head>`.
2. `api.service.ts`: add the interfaces from §7.2 and the auth methods. **Auth calls pass
   `{ withCredentials: true }`** so the browser stores and sends the refresh cookie:
   `getAuthConfig()`, `register()`, `login()`, `loginWithGoogle(credential)`, `refresh()`,
   `logout()`, `me()`.
3. `auth.service.ts` (`providedIn: 'root'`):
   ```ts
   readonly user = signal<User | null>(null);
   readonly isLoggedIn = computed(() => this.user() !== null);
   private accessToken: string | null = null;     // memory only; never localStorage

   getAccessToken(): string | null
   handleTokenResponse(r: TokenResponse): void    // store token + user
   restoreSession(): Promise<void>                // calls refresh(); on error stays logged out. Never throws.
   refreshAccessToken(): Observable<string>       // share one in-flight refresh (shareReplay(1)) so parallel 401s refresh once
   logout(): void                                 // api.logout(), clear state, router.navigate(['/login'])
   ```
4. `app.config.ts`:
   ```ts
   provideRouter(routes, withComponentInputBinding()),
   provideHttpClient(withFetch(), withInterceptors([authInterceptor])),
   { provide: APP_INITIALIZER, multi: true,
     useFactory: () => { const auth = inject(AuthService); return () => auth.restoreSession(); } },
   ```
   This makes a page reload stay logged in (the cookie survives; the in-memory token doesn't).
5. `auth.interceptor.ts` (functional `HttpInterceptorFn`):
   - Add `Authorization: Bearer <token>` if a token exists and the URL is not `/auth/`.
   - On **401** from a non-`/auth/` URL: call `auth.refreshAccessToken()` **once**, retry
     the request with the new token; if the refresh fails → `auth.logout()` and rethrow.
   - Never retry `/auth/refresh` itself (avoids an infinite loop).
6. `auth.guards.ts`: `authGuard` (logged out → `/login?returnUrl=…`), `guestGuard` (logged in → `/cases`).
7. `google-signin-button.component.ts`:
   ```ts
   // The Google Identity Services script has no bundled typings, and adding @types would be a
   // new dependency, so this declares only the part we use.
   interface GoogleAccountsId {
     initialize(cfg: { client_id: string; callback: (r: { credential: string }) => void }): void;
     renderButton(el: HTMLElement, opts: Record<string, unknown>): void;
   }
   declare const google: { accounts: { id: GoogleAccountsId } };
   ```
   - On init: `api.getAuthConfig()`. If `google_client_id` is null, render nothing.
   - Wait until `typeof google !== 'undefined'` (the script is async; poll every 100 ms, up to
     5 s, and clear the timer in `ngOnDestroy`).
   - `initialize({client_id, callback})`, then `renderButton(this.host.nativeElement, {theme: 'filled_black', size: 'large', width: 320})`.
   - The callback runs **outside Angular's zone**: wrap it in `this.zone.run(() => this.credential.emit(r.credential))`.
8. `login.page.ts` / `register.page.ts`: reactive form (`@angular/forms` is already a
   dependency), shows the server error message, Google button underneath. On success:
   `auth.handleTokenResponse(r)` then navigate to `returnUrl` or `/cases`.
9. `app.routes.ts`:
   ```ts
   { path: 'login',    canActivate: [guestGuard], loadComponent: () => import('./pages/login.page').then(m => m.LoginPage) },
   { path: 'register', canActivate: [guestGuard], loadComponent: ... },
   { path: 'cases',    canActivate: [authGuard],  loadComponent: ... CasesDashboardPage },   // Phase 6
   { path: 'cases/:caseId', canActivate: [authGuard], loadComponent: ... CaseWorkspacePage }, // Phase 6
   { path: 'workspaces/:workspaceId/members', canActivate: [authGuard], loadComponent: ... }, // Phase 6
   { path: '', pathMatch: 'full', redirectTo: 'cases' },
   { path: '**', redirectTo: 'cases' },
   ```
   Until Phase 6 exists, point `cases` at a temporary page showing "Logged in as {{ user.email }}" + a logout button.

**Done when:** `npx ng build --configuration development` passes, and manually in the browser:
register → land on /cases; reload → still logged in; logout → /login; visiting /cases while
logged out → /login; Google button → account chooser → logged in, and a `users` row with an
`oauth_accounts` row exists.

---

### Phase 6: Frontend cases and case-scoped UI

**Files:** `app.component.ts` (slimmed), `pages/cases-dashboard.page.ts` (new),
`pages/case-workspace.page.ts` (new; takes the old AppComponent body),
`pages/workspace-members.page.ts` (new), `services/case-context.service.ts` (new),
`services/api.service.ts`, `components/sidebar.component.ts`, `upload-modal.component.ts`,
`graph-view.component.ts`, `qa-panel.component.ts`.

1. **Move, don't rewrite.** Cut the existing shell template, styles and logic from
   `AppComponent` into `CaseWorkspacePage`. `AppComponent` becomes a slim top bar (brand,
   user name, logout) + `<router-outlet/>`.
2. `case-context.service.ts`: `readonly caseId = signal<string | null>(null);` plus
   `readonly case = signal<Case | null>(null)`. `CaseWorkspacePage` has `@Input() caseId!: string`
   (bound from the route by `withComponentInputBinding`) and sets the context in `ngOnInit`.
   It loads `getCase(caseId)`; on 404 it navigates to `/cases`.
3. `api.service.ts`: every document, graph and query method gains a **first** parameter
   `caseId: string` and uses `${this.base}/cases/${caseId}/…`. Add the workspace, case,
   member, query-log and audit methods (§7.2).
4. The four existing components read `this.ctx.caseId()!` and pass it through. No other logic
   changes. Wherever a component polls, check that it already clears its interval in
   `ngOnDestroy` (it's now destroyed on navigation).
5. `cases-dashboard.page.ts`:
   - a workspace switcher (dropdown of `GET /workspaces`, remembered in `localStorage`
     inside try/catch) and a "New organization" button
   - a case list as cards: title, reference code, status chip, priority chip, document count,
     updated date. Filter by status. Click → `/cases/:id`
   - a "New case" dialog (title, reference code, description, priority). Hide it for `viewer`
   - a link to members (shown to admin/owner)
6. `case-workspace.page.ts` additions: a case header (title, status dropdown for member+,
   priority), a "History" side tab listing `GET /cases/{id}/queries`, upload hidden for viewers.
7. `workspace-members.page.ts`: table of members; admin+ can add by email + role, change role
   and remove. Show API errors (404 "no such user", 409 already member).
8. Status/priority chip colours: add CSS custom properties to `src/styles.scss` (e.g.
   `--status-open`, `--prio-critical`) and use those. No hex values in components.

**Done when:** `npx ng build --configuration development` passes; the full flow works in the
browser: login → create case → upload `dhaka.txt` → graph appears → ask a question in all 4
modes → History tab shows them → second case is empty → viewer account can read but sees no
upload/delete controls.

---

### Phase 7: Documentation and final check

1. **ARCHITECTURE.md**: update §1 (investigation tool), §2 (remove auth, persistence and
   isolation from the non-goals; add "no email verification / password reset, no rate
   limiting"), §3 diagram, §4 new modules, §5 flows (upload is case-scoped and persists; a
   query is logged), §6.1 and §6.2 replaced by §5 of this plan, §7 add Postgres / SQLAlchemy /
   Alembic / PyJWT / pwdlib / google-auth, §8 (remove 1 and 6; add "snapshot-replace on every
   index", "per-case shared chat history", "LRU index cache"), §9 add:
   - **D11** One pipeline bundle per case, loaded lazily from Postgres (supersedes D2)
   - **D12** Pipeline is persistence-agnostic (`export_state` / `load_state`)
   - **D13** Access = workspace membership + role; non-members get 404
   - **D14** JWT access token in memory + rotating httpOnly refresh cookie
2. **CLAUDE.md**: add to "Running things": `alembic upgrade head`; how to create a migration
   (`alembic revision --autogenerate -m "…"`, then review it); add `alembic/versions/` to the
   public-interface list ("never edit an applied migration; add a new one"); add a
   verification row "DB schema → `alembic upgrade head` on a fresh DB". Add to the hard rules:
   never log tokens, passwords or Google credentials.
3. `.env.example` complete (Phase 0).
4. Full end-to-end run of the Phase 4 and Phase 6 "Done when" lists, plus
   `python -m evaluation.eval` on 1–2 QA pairs to prove the eval CLI still works without the DB.

---

## 7. Reference: frontend TypeScript interfaces (mirror of §5.3)

### 7.1 Unchanged
`GraphNode`, `GraphEdge`, `GraphData`, `GraphStats`, `EntityDetail`, `QueryResponse`.

### 7.2 New / changed
```ts
export interface User { id: string; email: string; full_name: string; email_verified: boolean; has_password: boolean; created_at: string; }
export interface TokenResponse { access_token: string; token_type: 'bearer'; expires_in: number; user: User; }
export type Role = 'owner' | 'admin' | 'member' | 'viewer';
export interface Workspace { id: string; name: string; kind: 'personal' | 'organization'; my_role: Role; created_at: string; }
export interface Member { user_id: string; email: string; full_name: string; role: Role; created_at: string; }
export type CaseStatus = 'open' | 'in_progress' | 'closed' | 'archived';
export type CasePriority = 'low' | 'medium' | 'high' | 'critical';
export interface Case {
  id: string; workspace_id: string; reference_code: string | null; title: string; description: string;
  status: CaseStatus; priority: CasePriority; created_by: string | null;
  created_at: string; updated_at: string; closed_at: string | null; document_count: number;
}
export interface DocRecord {   // existing fields + new ones
  id: string; filename: string; content_type: string; size_bytes: number;
  status: 'uploaded' | 'indexing' | 'indexed' | 'error'; error?: string; chunks: number;
  case_id: string; sha256: string; uploaded_by: string | null; created_at: string | null;
}
export interface QueryLog {
  id: string; question: string; method: string; answer: string; confidence: number;
  entities: { id: string; name: string; type: string }[]; sources: string[];
  latency_ms: number; user_id: string | null; created_at: string;
}
export interface AuditEvent { id: number; action: string; user_id: string | null; target_type: string | null; target_id: string | null; details: Record<string, unknown>; created_at: string; }
```

---

## 8. Security checklist (verify before calling the work done)

- [ ] Passwords hashed with argon2; no plaintext anywhere (DB, logs, responses).
- [ ] Refresh tokens stored as SHA-256 only; rotated on every use; reuse revokes all the user's tokens.
- [ ] The refresh cookie is `HttpOnly`, `SameSite=Lax`, `Path=/api/auth`; `Secure` is controlled by `COOKIE_SECURE`.
- [ ] `jwt.decode` always gets `algorithms=[...]`; the token `type` claim is checked.
- [ ] The Google ID token is verified on the server (signature, `aud` = our client ID, `iss`, `exp`, `email_verified`).
- [ ] Login failures return one generic message (no user enumeration through login).
- [ ] Every case-scoped query filters by `case_id`; a document is fetched by `(case_id, doc_id)`, never by `doc_id` alone.
- [ ] Non-members get 404, not 403.
- [ ] Upload filenames are sanitised; files live under `UPLOAD_DIR/<case_id>/`; `stored_path` is never returned.
- [ ] CORS origins are explicit (no `*` with credentials).
- [ ] `JWT_SECRET_KEY` missing → the server refuses to start.
- [ ] Nothing reads or prints `.env`; `.env.example` has placeholders only.
- [ ] Known, accepted gaps (write them in ARCHITECTURE.md §8): no rate limiting on login,
      no email verification or password reset, no CSRF token (mitigated by SameSite=Lax and
      the refresh endpoint returning only a token in the body).

---

## 9. Later phase (outline only, NOT part of this work): proving the improvement over naive RAG

Do this only after Phases 0–7 are done and the owner asks for it. It needs approval, because
it changes the `method` literal (a public interface) and the eval CLI.

1. **Baseline method `naive`**: classic RAG = embed the query, cosine top-k over **chunk**
   embeddings, stuff them into the same `_ANSWER_PROMPT`. The HiPPO level-0 nodes already
   *are* plain chunk embeddings, so the baseline can be implemented as a small
   `NaiveRetriever` (or `hippo.retrieve(level=0)`) with no new indexing cost. Using the same
   chunks (D4), prompt and LLM keeps the comparison fair: the only variable is retrieval.
2. Migration: add `'naive'` to the `query_logs.method` CHECK; add it to `QueryRequest.method`,
   the eval `--method` choices and the UI selector.
3. **Compare mode** in the UI: one question → all 5 methods in parallel → side-by-side
   answers, latency and confidence (data comes from `query_logs`).
4. **Eval persistence**: tables `eval_runs(id, case_id NULL, created_by, method, qa_file, created_at, summary JSONB)`
   and `eval_results(run_id, question, reference, answer, rouge_l, bleu1, entity_recall, faithfulness, latency_ms)`.
   Keep the CLI standalone; add an optional `--save-db` flag later if wanted.
5. Use the sample cases in `data/input/sample_cases/` with a hand-written QA set per case,
   including **multi-hop** and **global** ("summarise all…") questions, since that's where
   GraphRAG and RAPTOR should beat naive RAG. Report a mean ± std per metric per method.

---

## 10. Out of scope for this plan

Email verification, password reset, invitations by email, rate limiting, SSO beyond Google,
un-indexing on delete (§8.2), pgvector, horizontal scaling, HTTPS deployment, automated tests
(pytest still needs separate approval: worth proposing once Phase 4 lands, because
`export_state`/`load_state` round-trips are ideal unit tests).

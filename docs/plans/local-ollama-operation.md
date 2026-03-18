# Plan: Fully Local Operation with Ollama

> **Goal**: Run Cerefox entirely locally — no Supabase, no OpenAI API, no cloud
> dependencies. Use PostgreSQL + pgvector (Docker) for storage and Ollama for embeddings.

---

## Current Architecture (Cloud)

```
Ingest:   CLI/Web UI → Python → OpenAI API (embed) → Supabase Postgres
Search:   CLI/Web UI → Python → OpenAI API (embed query) → Supabase RPC → results
Agent:    MCP client → cerefox-mcp Edge Function → cerefox-search/ingest EFs → OpenAI → DB
```

**Cloud dependencies**:
1. **Supabase Postgres** — stores documents, chunks, vectors
2. **OpenAI API** — generates 768-dim embeddings (text-embedding-3-small)
3. **Supabase Edge Functions** — server-side embedding + search/ingest for agents

---

## Target Architecture (Local)

```
Ingest:     CLI/Web UI → Python → Ollama (embed) → local Postgres+pgvector
Search:     CLI/Web UI → Python → Ollama (embed query) → local Postgres RPC → results
Local agent:  MCP client → cerefox mcp (stdio) → Ollama → local Postgres
Remote agent: MCP client → cerefox mcp (HTTP, port 8001) → Ollama → local Postgres
Any HTTP client: POST /api/search, /api/ingest → FastAPI → Ollama → local Postgres
```

**Zero cloud dependencies.** Everything runs on localhost. External agents connect
over the network via MCP HTTP or REST API.

---

## What Already Works Locally

These components are **cloud-agnostic** and need no changes:

| Component | Location | Why it works |
|-----------|----------|-------------|
| PostgreSQL + pgvector | `docker-compose.yml` | Already defines a local `pgvector/pgvector:pg16` container |
| Database schema | `src/cerefox/db/schema.sql` | Vanilla Postgres + pgvector; `VECTOR(768)` is model-agnostic |
| Search RPCs | `src/cerefox/db/rpcs.sql` | Cosine distance (`<=>`) works with any normalized 768-dim vectors |
| Schema deployment | `scripts/db_deploy.py` | Connects via `CEREFOX_DATABASE_URL` (psycopg2), not Supabase API |
| Markdown chunking | `src/cerefox/chunking/markdown.py` | No embedding dependency |
| Ingestion pipeline | `src/cerefox/ingestion/pipeline.py` | Receives an `Embedder` instance — pluggable |
| Search client | `src/cerefox/retrieval/search.py` | Receives an `Embedder` instance — pluggable |
| FastAPI web UI | `src/cerefox/api/` | All routes, templates, HTMX — embedding-agnostic |
| CLI | `src/cerefox/cli.py` | Uses factory functions for embedder — just needs a new branch |
| Local MCP server | `src/cerefox/mcp_server.py` | stdio transport, already works without Edge Functions |
| Backup system | `src/cerefox/backup/fs_backup.py` | File-based, no cloud dependency |

---

## What Supabase Edge Functions Do (and What Replaces Them)

There are **4 Edge Functions** deployed to Supabase (`supabase/functions/*/index.ts`).
They exist so that **remote agents** can search/ingest without running Python or an
embedding model locally. In a fully local setup, they are **not needed** — the local
Python MCP server and FastAPI app cover all the same functionality.

### cerefox-search

- **What it does**: Accepts a text query via HTTP POST, embeds it server-side using
  OpenAI `text-embedding-3-small`, calls the `cerefox_hybrid_search` /
  `cerefox_search_docs` / `cerefox_fts_search` RPC, enforces byte budget, returns
  ranked results.
- **Parameters**: `query`, `project_name`, `match_count`, `mode` (hybrid/fts/docs),
  `alpha`, `min_score`, `max_bytes`
- **Why it exists**: Agents (Claude Code, Cursor, ChatGPT via GPT Actions) need
  search without installing Python or an embedding model.
- **Local replacement**: Already covered by:
  - `cerefox search` CLI command (calls `SearchClient` → embeds query → calls RPC)
  - Web UI `/search` route (same path)
  - `cerefox mcp` server exposes `cerefox_search` tool over stdio

### cerefox-ingest

- **What it does**: Accepts `title` + `content` via HTTP POST, normalizes content,
  computes SHA-256 for dedup, chunks markdown (heading-aware, 4000 char max), embeds
  all chunks server-side, inserts document + chunks into DB. Supports
  `update_if_exists` for idempotent updates.
- **Parameters**: `title`, `content`, `project_name`, `source`, `metadata`,
  `update_if_exists`
- **Why it exists**: Agents can save notes to the knowledge base without local Python.
  Server-side chunking + embedding keeps agent setup zero-dependency.
- **Local replacement**: Already covered by:
  - `cerefox ingest` CLI command (calls `IngestionPipeline`)
  - Web UI `/ingest` route (paste or file upload)
  - `cerefox mcp` server exposes `cerefox_ingest` tool over stdio

### cerefox-metadata

- **What it does**: Calls `cerefox_list_metadata_keys()` RPC, returns all distinct
  metadata keys in use across documents with doc counts and example values.
- **Parameters**: none
- **Why it exists**: Agents discover the metadata vocabulary before ingesting.
- **Local replacement**: Already covered by:
  - `cerefox list-metadata-keys` CLI command
  - `cerefox mcp` server exposes `cerefox_list_metadata_keys` tool

### cerefox-mcp

- **What it does**: MCP Streamable HTTP adapter. Wraps the other three Edge Functions
  as a single remote MCP endpoint (JSON-RPC 2.0 over HTTP). Handles `initialize`,
  `tools/list`, `tools/call`, `ping`. Delegates tool calls to sibling Edge Functions.
- **Exposed tools**: `cerefox_search`, `cerefox_ingest`, `cerefox_list_metadata_keys`
- **Why it exists**: Single HTTPS URL for all MCP clients. Claude Code uses
  `--transport http`, Cursor uses native URL config, Claude Desktop uses `supergateway`
  as stdio-to-HTTP bridge.
- **Local replacement**: `cerefox mcp` (stdio) — already implemented. All desktop MCP
  clients (Claude Desktop, Claude Code, Cursor) can launch it as a subprocess. No HTTP
  transport needed for local use.

### Summary

| Edge Function | Purpose | Local Equivalent | Status |
|--------------|---------|------------------|--------|
| cerefox-search | Server-side search + embed | CLI, Web UI, MCP server | Already exists |
| cerefox-ingest | Server-side ingest + embed | CLI, Web UI, MCP server | Already exists |
| cerefox-metadata | Metadata key discovery | CLI, MCP server | Already exists |
| cerefox-mcp | Remote MCP HTTP endpoint | `cerefox mcp` (stdio only) | **Needs HTTP transport** |

**Conclusion**: Edge Functions are a remote convenience layer. For local operation,
the existing Python code covers all the same logic — but the MCP server only speaks
stdio, so external/remote agents can't reach it. Two gaps need filling:

1. **MCP HTTP transport** — so remote MCP clients (Claude Code on another machine,
   Cursor, any Streamable HTTP client) can connect over the network
2. **JSON REST API** — so non-MCP agents (ChatGPT via GPT Actions, curl scripts,
   custom integrations) can call search/ingest over plain HTTP

---

## What Needs to Change

### 1. External Agent Access (replaces Edge Functions)

Currently the FastAPI app only serves HTML (Jinja2 + HTMX) and the MCP server only
speaks stdio. External agents on the network have no way in. Two things fix this:

#### A. JSON REST API endpoints on FastAPI

Add programmatic JSON endpoints alongside the existing HTML routes. These are the
local equivalent of the cerefox-search / cerefox-ingest / cerefox-metadata Edge
Functions — same request/response contracts, same functionality, but served by the
local FastAPI app.

**File**: `src/cerefox/api/routes.py` (or a new `src/cerefox/api/api_routes.py`)

| Endpoint | Method | Replaces | What it does |
|----------|--------|----------|-------------|
| `/api/search` | POST | cerefox-search EF | Accept `{query, project_name?, match_count?, mode?, alpha?, min_score?, max_bytes?}`, embed query via Ollama, call RPC, return JSON results |
| `/api/ingest` | POST | cerefox-ingest EF | Accept `{title, content, project_name?, source?, metadata?, update_if_exists?}`, chunk + embed + store, return JSON result |
| `/api/metadata` | POST | cerefox-metadata EF | Call `cerefox_list_metadata_keys()` RPC, return JSON array |

These endpoints reuse the existing `IngestionPipeline`, `SearchClient`, and
`CerefoxClient` — no new business logic needed. They're thin JSON wrappers around
the same code the CLI and web UI already call.

**Who uses this**: ChatGPT (GPT Actions), curl scripts, any HTTP client, custom
agent integrations that don't speak MCP.

#### B. MCP Streamable HTTP transport

The MCP Python SDK (`mcp>=1.26`) already ships `StreamableHTTPServerTransport`.
Add an HTTP transport option to the existing MCP server so remote MCP clients
can connect over the network — same tools, same logic, different wire protocol.

**Implementation options** (pick one):

1. **Standalone HTTP server** — `cerefox mcp --transport http --port 8001`
   Runs uvicorn serving the MCP Streamable HTTP endpoint. Simple, isolated.

2. **Mount on FastAPI** — add an `/mcp` route to the existing FastAPI app.
   One process, one port (8000), serves both web UI and MCP. Simpler deployment
   but couples the MCP server lifecycle to the web app.

Option 1 is cleaner — keeps MCP and web UI independent, avoids port conflicts,
and mirrors how `cerefox mcp` already works (standalone process).

**File**: `src/cerefox/mcp_server.py` — add HTTP transport path alongside stdio.

**Who uses this**: Claude Code (`--transport http`), Cursor (native URL config),
Claude Desktop (via `supergateway` bridge), any MCP Streamable HTTP client on the
network.

#### C. Optional: Bearer token auth

For LAN/public exposure, add a simple `CEREFOX_API_TOKEN` setting. If set,
all `/api/*` and MCP HTTP requests must include `Authorization: Bearer <token>`.
If unset, no auth (localhost-only use). Keeps it simple — no OAuth, no user
management.

---

### 2. New `OllamaEmbedder` class

**File**: `src/cerefox/embeddings/ollama.py` (~80 lines)

Implements the `Embedder` protocol (`embeddings/base.py`):

```python
class OllamaEmbedder:
    dimensions: int          # 768
    model_name: str          # "nomic-embed-text"

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

Calls Ollama's `POST /api/embed` endpoint:
```json
{
  "model": "nomic-embed-text",
  "input": ["text to embed"]
}
```

Response: `{ "embeddings": [[0.1, 0.2, ...]] }`

No new Python dependencies needed — uses `httpx` (already a core dep).

### 3. Config additions

**File**: `src/cerefox/config.py`

```python
# Ollama settings
embedder: Literal["openai", "fireworks", "ollama"] = "openai"
ollama_base_url: str = "http://localhost:11434"
ollama_embedding_model: str = "nomic-embed-text"

# API token (optional — for external agent auth)
api_token: str = ""          # if set, required as Bearer token on /api/* and MCP HTTP

# MCP HTTP transport
mcp_http_port: int = 8001    # port for MCP Streamable HTTP server
```

Add `"ollama"` to `get_embedder_*()` helper methods.

### 4. Embedder factory

**Files**: 3 locations where embedder is instantiated

- `src/cerefox/api/routes.py` → `_cached_embedder()`
- `src/cerefox/cli.py` → `_get_embedder()`
- `src/cerefox/mcp_server.py` → inline creation

Add an `if settings.embedder == "ollama"` branch. Ideally extract a shared
`create_embedder(settings)` factory to avoid repeating the logic in 3 places.

### 5. Docker (optional)

Add Ollama as a service in `docker-compose.yml`, or point at a host-running
Ollama instance via `CEREFOX_OLLAMA_BASE_URL`.

### 6. Tests

- Unit tests for `OllamaEmbedder` (mock httpx responses)
- Unit tests for JSON API endpoints (mock embedder + DB)
- Integration test with live Ollama (optional, marked `@pytest.mark.e2e`)

### 7. Documentation

- Update `docs/guides/setup-local.md` with Ollama setup steps
- Update `docs/guides/configuration.md` with new env vars
- Update `docs/guides/connect-agents.md` with local HTTP paths
- Update `.env.example`

---

## Recommended Ollama Model

The schema uses `VECTOR(768)` — the model must output **768 dimensions** natively.

| Model | Dims | Context | Size | Notes |
|-------|------|---------|------|-------|
| **`nomic-embed-text`** | **768** | 8192 tok | 274 MB | **Recommended.** Same model already used via Fireworks (`nomic-ai/nomic-embed-text-v1.5`). High quality, fast, battle-tested in this project. |
| `snowflake-arctic-embed:335m` | 768 | 512 tok | 670 MB | 768-dim but shorter context window and larger download. |
| `mxbai-embed-large` | 1024 | 512 tok | 670 MB | Good quality but 1024-dim — requires schema change. |
| `all-minilm` | 384 | 512 tok | 46 MB | 384-dim — requires schema change. Tiny but low quality. |

### Why `nomic-embed-text`

1. **768 dimensions** — matches `VECTOR(768)` schema exactly, no migration needed
2. **Same model family** as the Fireworks path (`nomic-embed-text-v1.5`)
3. **8192 token context** — handles full chunks (4000 chars ≈ ~1000-1500 tokens)
4. **274 MB** — small enough for any machine
5. **Well-supported** by Ollama with frequent updates

### Setup

```bash
ollama pull nomic-embed-text
```

---

## Scope Summary

| Category | Files Changed | Effort |
|----------|--------------|--------|
| JSON REST API endpoints | 1 new file (~150 lines) | Medium |
| MCP HTTP transport | 1 file modified (~60 lines) | Small |
| New OllamaEmbedder class | 1 new file (~80 lines) | Small |
| Config additions | 1 file (~15 lines) | Trivial |
| Embedder factory | 3 files (~5 lines each) | Small |
| Optional bearer auth | ~20 lines across API + MCP | Trivial |
| Tests | 2 new files (~150 lines) | Medium |
| Docs updates | 4 files | Small |
| Docker (optional) | 1 file | Trivial |
| **Total** | **~500 lines of new code** | **Medium** |

Everything else (schema, RPCs, chunking, ingestion, search, web UI, CLI, backup)
works unchanged.

---

## `.env` for Fully Local Operation

```bash
# Database — local Docker Postgres
CEREFOX_DATABASE_URL=postgresql://cerefox:cerefox@localhost:5432/cerefox
CEREFOX_SUPABASE_URL=              # empty — not needed
CEREFOX_SUPABASE_KEY=              # empty — not needed

# Embeddings — local Ollama
CEREFOX_EMBEDDER=ollama
CEREFOX_OLLAMA_BASE_URL=http://localhost:11434
CEREFOX_OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# No cloud API keys needed
OPENAI_API_KEY=                    # empty — not needed

# External agent access (optional)
CEREFOX_API_TOKEN=                 # empty = no auth (localhost only)
CEREFOX_MCP_HTTP_PORT=8001         # MCP Streamable HTTP port
```

---

## How External Agents Connect (Local)

### MCP clients (Claude Code, Cursor, Claude Desktop)

Start the MCP HTTP server:
```bash
cerefox mcp --transport http --port 8001
```

Claude Code:
```bash
claude mcp add cerefox --transport http http://YOUR_IP:8001/mcp
```

Cursor (`mcp.json`):
```json
{
  "cerefox": {
    "url": "http://YOUR_IP:8001/mcp",
    "headers": { "Authorization": "Bearer <token>" }
  }
}
```

Claude Desktop (via supergateway bridge):
```json
{
  "mcpServers": {
    "cerefox": {
      "command": "npx",
      "args": ["-y", "supergateway", "--sse", "http://YOUR_IP:8001/mcp"]
    }
  }
}
```

### Non-MCP agents (ChatGPT GPT Actions, curl, custom code)

The JSON REST API runs on the same FastAPI app as the web UI (port 8000):

```bash
# Search
curl -X POST http://YOUR_IP:8000/api/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query": "how does ingestion work", "match_count": 5}'

# Ingest
curl -X POST http://YOUR_IP:8000/api/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"title": "My Note", "content": "# Hello\nSome content", "project_name": "notes"}'

# List metadata keys
curl -X POST http://YOUR_IP:8000/api/metadata \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{}'
```

For ChatGPT GPT Actions, point the OpenAPI schema at `http://YOUR_IP:8000/api/`
with API key auth using `CEREFOX_API_TOKEN`.

---

## Access Path Comparison

| Access Path | Protocol | Who Uses It | Runs Where |
|-------------|----------|-------------|-----------|
| `cerefox mcp` (stdio) | MCP stdio | Local-only agents (Claude Desktop subprocess) | Same machine |
| `cerefox mcp --transport http` | MCP Streamable HTTP | Claude Code, Cursor, Claude Desktop (via supergateway), any MCP client | Any machine on network |
| `/api/search`, `/api/ingest` | REST JSON over HTTP | ChatGPT GPT Actions, curl, custom agents, any HTTP client | Any machine on network |
| Web UI (port 8000) | HTML + HTMX | Humans in a browser | Any machine on network |
| Edge Functions (Supabase) | HTTP / MCP Streamable HTTP | All of the above, hosted remotely | Cloud (not needed locally) |
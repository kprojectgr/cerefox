# Plan: Fully Local Operation with Ollama

> **Goal**: Run Cerefox entirely locally — no Supabase, no OpenAI API, no cloud
> dependencies. Use PostgreSQL + pgvector (Docker) for storage and Ollama for embeddings.
> External agents connect over the network via JSON REST API or MCP Streamable HTTP.

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
Ingest:       CLI/Web UI → Python → Ollama (embed) → local Postgres+pgvector
Search:       CLI/Web UI → Python → Ollama (embed query) → local Postgres RPC → results
Local agent:  MCP client → cerefox mcp (stdio) → Ollama → local Postgres
Remote agent: MCP client → cerefox mcp (HTTP, port 8001) → Ollama → local Postgres
Any HTTP:     POST /api/search, /api/ingest → FastAPI → Ollama → local Postgres
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
embedding model locally. In a fully local setup, they are **not needed** — but we need
local equivalents for the remote access they provide.

### cerefox-search

- **What it does**: Accepts a text query via HTTP POST, embeds it server-side using
  OpenAI `text-embedding-3-small`, calls the `cerefox_hybrid_search` /
  `cerefox_search_docs` / `cerefox_fts_search` RPC, enforces byte budget, returns
  ranked results.
- **Parameters**: `query`, `project_name`, `match_count`, `mode` (hybrid/fts/docs),
  `alpha`, `min_score`, `max_bytes`
- **Why it exists**: Agents (Claude Code, Cursor, ChatGPT via GPT Actions) need
  search without installing Python or an embedding model.
- **Local replacement**:
  - Same-machine: `cerefox mcp` (stdio) exposes `cerefox_search` tool
  - Network: new `/api/search` JSON endpoint + MCP HTTP transport

### cerefox-ingest

- **What it does**: Accepts `title` + `content` via HTTP POST, normalizes content,
  computes SHA-256 for dedup, chunks markdown (heading-aware, 4000 char max), embeds
  all chunks server-side, inserts document + chunks into DB. Supports
  `update_if_exists` for idempotent updates.
- **Parameters**: `title`, `content`, `project_name`, `source`, `metadata`,
  `update_if_exists`
- **Why it exists**: Agents can save notes to the knowledge base without local Python.
  Server-side chunking + embedding keeps agent setup zero-dependency.
- **Local replacement**:
  - Same-machine: `cerefox mcp` (stdio) exposes `cerefox_ingest` tool
  - Network: new `/api/ingest` JSON endpoint + MCP HTTP transport

### cerefox-metadata

- **What it does**: Calls `cerefox_list_metadata_keys()` RPC, returns all distinct
  metadata keys in use across documents with doc counts and example values.
- **Parameters**: none
- **Why it exists**: Agents discover the metadata vocabulary before ingesting.
- **Local replacement**:
  - Same-machine: `cerefox mcp` (stdio) exposes `cerefox_list_metadata_keys` tool
  - Network: new `/api/metadata` JSON endpoint + MCP HTTP transport

### cerefox-mcp

- **What it does**: MCP Streamable HTTP adapter. Wraps the other three Edge Functions
  as a single remote MCP endpoint (JSON-RPC 2.0 over HTTP). Handles `initialize`,
  `tools/list`, `tools/call`, `ping`. Delegates tool calls to sibling Edge Functions.
- **Exposed tools**: `cerefox_search`, `cerefox_ingest`, `cerefox_list_metadata_keys`
- **Why it exists**: Single HTTPS URL for all MCP clients. Claude Code uses
  `--transport http`, Cursor uses native URL config, Claude Desktop uses `supergateway`
  as stdio-to-HTTP bridge.
- **Local replacement**: `cerefox mcp --transport http` — add Streamable HTTP
  transport to the existing MCP server using the MCP Python SDK's
  `StreamableHTTPServerTransport` (already available in `mcp>=1.26`).

### Summary

| Edge Function | Purpose | Local Equivalent | Status |
|--------------|---------|------------------|--------|
| cerefox-search | Server-side search + embed | `/api/search` JSON endpoint | **Needs building** |
| cerefox-ingest | Server-side ingest + embed | `/api/ingest` JSON endpoint | **Needs building** |
| cerefox-metadata | Metadata key discovery | `/api/metadata` JSON endpoint | **Needs building** |
| cerefox-mcp | Remote MCP HTTP endpoint | `cerefox mcp --transport http` | **Needs building** |

---

## What Needs to Change

### 1. Embedder factory (`src/cerefox/embeddings/factory.py`)

Currently `CloudEmbedder` is instantiated with identical code in 3 places:
- `src/cerefox/api/routes.py` → `_cached_embedder()` (permissive: returns `None` on failure)
- `src/cerefox/cli.py` → `_get_embedder()` (strict: calls `sys.exit(1)`)
- `src/cerefox/mcp_server.py` → `_get_deps()` (no error handling)

Extract a shared factory that handles both `CloudEmbedder` and `OllamaEmbedder`:

```python
# src/cerefox/embeddings/factory.py

def create_embedder(settings: Settings) -> Embedder:
    """Create the correct embedder based on settings.

    Raises ValueError if required config (e.g. API key) is missing.
    """
    if settings.embedder == "ollama":
        from cerefox.embeddings.ollama import OllamaEmbedder
        return OllamaEmbedder(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        )
    else:
        from cerefox.embeddings.cloud import CloudEmbedder
        api_key = settings.get_embedder_api_key()
        if not api_key:
            raise ValueError(f"API key not set for {settings.embedder} embedder")
        return CloudEmbedder(
            api_key=api_key,
            base_url=settings.get_embedder_base_url(),
            model=settings.get_embedder_model(),
            dimensions=settings.get_embedder_dimensions(),
        )
```

Each call site keeps its own error handling but delegates instantiation to the
factory. This avoids 3-way duplication and makes adding new embedders trivial.

### 2. New `OllamaEmbedder` class (`src/cerefox/embeddings/ollama.py`)

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

No new Python dependencies — uses `httpx` (already a core dep).

Ollama's `/api/embed` supports batch input natively, so `embed_batch()` can send
all texts in one request (no client-side batching needed like `CloudEmbedder`).
Add a configurable batch size for very large batches (Ollama may OOM on huge inputs).

### 3. Config additions (`src/cerefox/config.py`)

```python
# Expand embedder literal
embedder: Literal["openai", "fireworks", "ollama"] = "openai"

# Ollama settings
ollama_base_url: str = "http://localhost:11434"
ollama_embedding_model: str = "nomic-embed-text"

# API token (optional — for external agent auth)
api_token: str = ""          # if set, required as Bearer token on /api/* and MCP HTTP

# MCP HTTP transport
mcp_http_port: int = 8001    # port for MCP Streamable HTTP server
```

Update `get_embedder_*()` helpers to handle `"ollama"` — or let the factory
bypass them entirely (Ollama doesn't use API keys or OpenAI-compatible base URLs).

### 4. JSON REST API endpoints

**File**: `src/cerefox/api/api_routes.py` (new file, separate from HTML routes)

| Endpoint | Method | Replaces | What it does |
|----------|--------|----------|-------------|
| `/api/search` | POST | cerefox-search EF | Embed query, call RPC, return JSON results |
| `/api/ingest` | POST | cerefox-ingest EF | Chunk + embed + store, return JSON result |
| `/api/metadata` | POST | cerefox-metadata EF | List metadata keys, return JSON array |

These reuse `IngestionPipeline`, `SearchClient`, and `CerefoxClient` via FastAPI
dependency injection (same pattern as HTML routes). Pydantic models for request/
response validation.

Register on the existing FastAPI app in `app.py`:
```python
from cerefox.api.api_routes import api_router
app.include_router(api_router)
```

Optional bearer auth middleware: if `CEREFOX_API_TOKEN` is set, check
`Authorization: Bearer <token>` header on `/api/*` routes. Skip auth if unset.

### 5. MCP Streamable HTTP transport

**File**: `src/cerefox/mcp_server.py` — extend existing file.

Add a `--transport` flag to the CLI entry point:
- `cerefox mcp` → stdio (default, backward-compatible)
- `cerefox mcp --transport http --port 8001` → Streamable HTTP

The HTTP path uses `StreamableHTTPServerTransport` from the MCP SDK, served via
Starlette/uvicorn. Same `Server` instance, same tool handlers — just different
wire protocol.

### 6. Docker (optional)

Add Ollama as a service in `docker-compose.yml`, or point at a host-running
Ollama instance via `CEREFOX_OLLAMA_BASE_URL`.

### 7. Documentation

- Update `docs/guides/setup-local.md` with Ollama setup steps
- Update `docs/guides/configuration.md` with new env vars
- Update `docs/guides/connect-agents.md` with local HTTP paths
- Update `.env.example`

---

## Testing Strategy

### Existing test baseline

424 tests across 12 files. Key patterns the new tests must follow:

| Pattern | How it works | Files |
|---------|-------------|-------|
| DB mocking | `mock_supabase_client` fixture chains `.rpc()`, `.table()` calls | `tests/conftest.py` |
| Embedder mocking | `@patch("httpx.post")` returns fake response | `tests/embeddings/test_embedders.py` |
| FastAPI testing | `TestClient` + `app.dependency_overrides` for DI | `tests/api/test_routes.py` |
| MCP testing | `@patch("cerefox.mcp_server._get_deps")` injects mock deps | `tests/test_mcp_server.py` |
| CLI testing | `CliRunner.invoke()` with mocked client/embedder | `tests/test_cli.py` |
| E2E (API) | Real Supabase + real embedder, `@pytest.mark.e2e`, cleanup tracker | `tests/e2e/test_api_e2e.py` |
| E2E (UI) | Playwright + Chromium, `@pytest.mark.ui`, app at :8000 | `tests/e2e/test_ui_e2e.py` |

### New unit tests

All unit tests are fast, fully mocked, no network. Run with `uv run pytest`.

#### `tests/embeddings/test_ollama_embedder.py` (~120 lines)

Mirror the structure of `test_embedders.py`:

```
TestOllamaEmbedderProtocol
  - test_satisfies_embedder_protocol          # isinstance check
  - test_dimensions_property                  # returns 768
  - test_model_name_property                  # returns configured model

TestOllamaEmbed
  - test_embed_returns_vector                 # 768-dim list[float]
  - test_embed_posts_to_correct_url           # POST /api/embed
  - test_embed_sends_model_in_payload         # {"model": "nomic-embed-text", ...}
  - test_embed_http_error_raises              # RuntimeError on 4xx/5xx
  - test_embed_connection_error_raises        # RuntimeError on connection refused
  - test_embed_wrong_dimensions_raises        # response has != 768 dims

TestOllamaEmbedBatch
  - test_empty_input_returns_empty            # no API call
  - test_single_input                         # one vector returned
  - test_multiple_inputs_order_preserved      # N inputs → N vectors, correct order
  - test_large_batch_splits                   # respects batch size limit
```

Mock pattern: `@patch("httpx.post")` returning fake Ollama response:
```python
{"embeddings": [[0.1, 0.2, ...]]}
```

#### `tests/embeddings/test_factory.py` (~80 lines)

```
TestCreateEmbedder
  - test_openai_returns_cloud_embedder        # settings.embedder="openai"
  - test_fireworks_returns_cloud_embedder      # settings.embedder="fireworks"
  - test_ollama_returns_ollama_embedder        # settings.embedder="ollama"
  - test_openai_missing_key_raises            # ValueError
  - test_ollama_no_key_needed                 # doesn't require API key
```

#### `tests/api/test_api_routes.py` (~200 lines)

Mirror `test_routes.py` patterns — `TestClient` + dependency overrides:

```
TestApiSearch
  - test_search_returns_json                  # 200 + Content-Type: application/json
  - test_search_calls_embedder                # embeds query text
  - test_search_fts_mode_skips_embedding      # FTS doesn't need embedder
  - test_search_with_project_filter           # project_name → project_id resolution
  - test_search_empty_results                 # returns empty array
  - test_search_missing_query_422             # validation error
  - test_search_respects_max_bytes            # truncation

TestApiIngest
  - test_ingest_returns_json                  # 201 + document_id in response
  - test_ingest_created_action                # new document
  - test_ingest_skipped_action                # duplicate content hash
  - test_ingest_updated_action                # update_if_exists=true
  - test_ingest_missing_title_422             # validation error
  - test_ingest_missing_content_422           # validation error
  - test_ingest_with_project                  # assigns project
  - test_ingest_with_metadata                 # passes metadata through

TestApiMetadata
  - test_metadata_returns_json                # array of {key, doc_count, example_values}
  - test_metadata_empty                       # empty array when no docs

TestApiAuth
  - test_no_token_configured_allows_request   # CEREFOX_API_TOKEN="" → open access
  - test_valid_token_allows_request           # correct Bearer token → 200
  - test_invalid_token_rejects_request        # wrong token → 401
  - test_missing_token_rejects_request        # no Authorization header → 401
```

#### `tests/test_mcp_server.py` (extend existing, ~40 lines added)

Add tests for the HTTP transport entry point:

```
TestMcpHttpTransport
  - test_http_transport_starts_server         # verify uvicorn is called
  - test_http_transport_uses_configured_port  # reads mcp_http_port from settings
  - test_stdio_transport_is_default           # backward compat
```

#### `tests/test_cli.py` (extend existing, ~20 lines added)

```
TestMcpCommand
  - test_mcp_transport_http_flag              # --transport http accepted
  - test_mcp_transport_stdio_default          # no flag → stdio
  - test_mcp_port_flag                        # --port 9000
```

### New integration / e2e tests

These hit real services. Run with `uv run pytest -m e2e` or a new marker.

#### `tests/e2e/test_local_e2e.py` (~150 lines, new `@pytest.mark.local` marker)

Requires: Ollama running + local Postgres Docker container.

```
TestOllamaEmbedder
  - test_embed_real_text                      # embed "hello world", verify 768-dim
  - test_embed_batch_real                     # batch of 3 texts, verify shapes
  - test_embed_long_text                      # chunk-sized text (~4000 chars)

TestLocalIngestAndSearch
  - test_ingest_document                      # full pipeline: chunk → embed → store
  - test_search_finds_ingested_doc            # hybrid search returns the doc
  - test_fts_search_finds_ingested_doc        # keyword search works
  - test_dedup_skips_identical                # re-ingest same content → skipped

TestJsonApi
  - test_api_search_live                      # POST /api/search with real embedder
  - test_api_ingest_live                      # POST /api/ingest, verify in DB
  - test_api_metadata_live                    # POST /api/metadata after ingest

TestMcpHttp
  - test_mcp_http_initialize                  # JSON-RPC initialize handshake
  - test_mcp_http_tools_list                  # returns 3 tools
  - test_mcp_http_search                      # tools/call cerefox_search
  - test_mcp_http_ingest                      # tools/call cerefox_ingest
```

#### Pytest configuration additions (`pyproject.toml`)

```toml
markers = [
    # ... existing markers ...
    "local: local e2e tests requiring Ollama + local Postgres (run with -m local)",
]
addopts = "-m 'not integration and not e2e and not ui and not local'"
```

### Test matrix summary

| Suite | Command | Needs | New tests |
|-------|---------|-------|-----------|
| Unit | `uv run pytest` | Nothing (all mocked) | ~460 lines across 4 files |
| API e2e | `uv run pytest -m e2e` | Live Supabase + API key | (no changes) |
| UI e2e | `uv run pytest -m ui` | Web app at :8000 + Playwright | (no changes) |
| **Local e2e** | `uv run pytest -m local` | Ollama + local Postgres | ~150 lines, 1 file |

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
3. **8192 token context** — handles full chunks (4000 chars ~ 1000-1500 tokens)
4. **274 MB** — small enough for any machine
5. **Well-supported** by Ollama with frequent updates

### Setup

```bash
ollama pull nomic-embed-text
```

---

## Scope Summary

| Category | Files | Lines (approx) | Effort |
|----------|-------|----------------|--------|
| Embedder factory | 1 new (`embeddings/factory.py`) + 3 modified | ~40 new + ~15 changed | Small |
| OllamaEmbedder | 1 new (`embeddings/ollama.py`) | ~80 | Small |
| Config additions | 1 modified (`config.py`) | ~15 | Trivial |
| JSON REST API | 1 new (`api/api_routes.py`) + 1 modified (`api/app.py`) | ~150 | Medium |
| MCP HTTP transport | 1 modified (`mcp_server.py`) + 1 modified (`cli.py`) | ~60 | Small |
| Bearer token auth | ~20 across API + MCP | ~20 | Trivial |
| Unit tests | 3 new files + 2 extended | ~460 | Medium |
| Local e2e tests | 1 new file + pyproject.toml marker | ~160 | Medium |
| Docs | 4 files updated | ~100 | Small |
| Docker (optional) | 1 modified (`docker-compose.yml`) | ~10 | Trivial |
| **Total** | **~8 new files, ~7 modified** | **~1100 lines** | **Medium** |

Everything else (schema, RPCs, chunking, ingestion, search, web UI, backup)
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

---

## Implementation Order

Suggested sequence — each step is independently testable:

1. **Embedder factory** — extract `create_embedder()`, update 3 call sites, run
   existing 424 tests to verify no regressions
2. **OllamaEmbedder** — implement class + unit tests
3. **Config** — add Ollama + API token + MCP port settings
4. **JSON REST API** — add `api_routes.py` + unit tests
5. **MCP HTTP transport** — add `--transport http` + unit tests
6. **Bearer token auth** — add middleware + tests
7. **Local e2e tests** — write against running Ollama + Postgres
8. **Docker** — optional Ollama service in docker-compose
9. **Docs** — update setup guides, config reference, agent connection guide
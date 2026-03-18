<p align="center">
  <img src="web/static/cerefox_logo.jpg" alt="Cerefox" width="160">
</p>

# Cerefox

**User-owned shared memory for AI agents** — a persistent, curated knowledge layer that multiple AI tools can read and write, backed by Postgres + pgvector.

[![Apache 2.0 License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)

---

## What is Cerefox?

Cerefox is a **user-owned knowledge memory layer** — a persistent, curated knowledge base that sits between you and the AI tools you use.

The primary use case is **shared memory across AI agents**: knowledge written by one tool (Claude, ChatGPT, Cursor, or a custom agent) becomes immediately available to all others. This prevents context fragmentation — the same information doesn't have to be re-explained in every session.

- **Agent-first, not human-first** — AI agents are first-class citizens on both sides: they read *and* write; humans curate and validate
- **Own your data** — everything lives in a Postgres database you control (Supabase free tier or self-hosted)
- **Not a note-taking app** — Cerefox is knowledge *infrastructure*, not a replacement for Obsidian, Notion, or Bear; those tools handle authoring, Cerefox handles indexing and agent access
- **Hybrid search** — full-text + semantic search finds relevant knowledge even with fuzzy or conceptual queries
- **Any agent, anywhere** — remote MCP, local MCP (stdio or HTTP), JSON REST API, ChatGPT via Custom GPT
- **Run fully local** — Ollama for embeddings + Docker Postgres, zero cloud dependencies
- **Keep it cheap** — Supabase free tier + low-cost cloud embeddings, or free with Ollama; see `docs/guides/operational-cost.md`

---

## Features

| Feature | Details |
|---------|---------|
| **Hybrid search** | Combines full-text (BM25) + semantic (vector) search with a configurable alpha weight |
| **Heading-aware chunking** | Greedy section accumulation — H1/H2/H3 sections accumulate until MAX_CHUNK_CHARS; heading breadcrumb preserved per chunk |
| **Pluggable embeddings** | OpenAI `text-embedding-3-small` (default), Fireworks AI, or **Ollama** (fully local, no API key) |
| **Remote MCP endpoint** | `cerefox-mcp` Supabase Edge Function — MCP Streamable HTTP; connect Claude Desktop, Claude Code, or Cursor with just a URL and anon key; no Python install needed |
| **Local MCP server** | `cerefox mcp` — stdio (local subprocess) or `--transport http` (network access for remote agents) |
| **JSON REST API** | `POST /api/search`, `/api/ingest`, `/api/metadata` — programmatic access for any HTTP client (ChatGPT GPT Actions, curl, custom agents) |
| **Web UI** | FastAPI + Jinja2 + HTMX dashboard for browsing, searching, and ingesting |
| **Multi-format ingest** | `.md`, `.txt`, `.pdf` (pypdf), `.docx` (python-docx) |
| **Batch ingest** | `cerefox ingest-dir` recurses directories |
| **Deduplication** | SHA-256 content hash; re-ingesting the same file is a no-op |
| **Backup and restore** | JSON snapshots, optional git commit |
| **Small-to-big retrieval** | `cerefox_context_expand` RPC returns chunk neighbours for richer context |

---

## Getting Started

> **Full walkthrough**: `docs/guides/quickstart.md` — zero to first ingested document and connected agent in 15 minutes.

### 1. Clone and install

```bash
git clone https://github.com/yourname/cerefox.git
cd cerefox
uv sync
```

### 2a. Cloud path — Set up Supabase (free)

1. Sign up at [supabase.com](https://supabase.com) — a GitHub login works fine.
2. Create a new project. Give it a name (e.g. `cerefox`) and set a database password.
3. Configure `.env`:

```bash
cp .env.example .env
```

| Variable | Where to find it |
|---|---|
| `CEREFOX_SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `CEREFOX_SUPABASE_KEY` | Supabase → Settings → API → Secret keys → `default` |
| `CEREFOX_DATABASE_URL` | Supabase → Settings → Database → Connection string → **Session pooler** (port 5432) |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

### 2b. Local path — Docker + Ollama (zero cloud dependencies)

```bash
docker compose up -d postgres        # start local Postgres+pgvector
ollama pull nomic-embed-text         # download the embedding model
cp .env.example .env
```

Edit `.env`:
```env
CEREFOX_DATABASE_URL=postgresql://cerefox:cerefox@localhost:5432/cerefox
CEREFOX_EMBEDDER=ollama
# No OPENAI_API_KEY or Supabase keys needed
```

### 3. Deploy the schema

```bash
uv run python scripts/db_deploy.py
```

### 4. (Cloud only) Deploy the Edge Functions

Edge Functions handle server-side embedding so AI agents never need a local model. Requires the [Supabase CLI](https://supabase.com/docs/guides/cli). Skip this for local-only setups.

```bash
npx supabase functions deploy cerefox-search
npx supabase functions deploy cerefox-ingest
npx supabase functions deploy cerefox-mcp
npx supabase secrets set OPENAI_API_KEY=sk-...your-key...
```

### 5. Ingest a document and open the web UI

```bash
uv run cerefox ingest my-notes.md --title "My notes"
uv run cerefox web                # → http://localhost:8000
```

---

## Architecture

```
cerefox_documents     cerefox_chunks
─────────────────     ───────────────────────────────
id, title, source     id, document_id, chunk_index
content_hash          heading_path, heading_level
project_id            content, char_count
metadata (JSONB)      embedding_primary (VECTOR 768)
chunk_count           fts (TSVECTOR, generated)
```

Search RPCs (MCP tools): `cerefox_hybrid_search`, `cerefox_fts_search`,
`cerefox_semantic_search`, `cerefox_search_docs`, `cerefox_reconstruct_doc`,
`cerefox_context_expand`, `cerefox_save_note`

---

## Connecting AI agents

**Option 1 — Remote MCP (recommended)** — just a URL, an anon key, and `npx`:

The `cerefox-mcp` Supabase Edge Function speaks MCP Streamable HTTP. No Python, no local
repo clone — works from any machine with Node.js installed.

```bash
# Claude Code (native HTTP transport)
claude mcp add --transport http cerefox \
  https://<project-ref>.supabase.co/functions/v1/cerefox-mcp \
  --header "Authorization: Bearer <anon-key>"
```

For Claude Desktop, use [`supergateway`](https://www.npmjs.com/package/supergateway) as
a stdio-to-HTTP bridge in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cerefox": {
      "command": "npx",
      "args": [
        "-y", "supergateway",
        "--streamableHttp", "https://<project-ref>.supabase.co/functions/v1/cerefox-mcp",
        "--header", "Authorization: Bearer <anon-key>"
      ]
    }
  }
}
```

For Cursor, use `url` + `headers.Authorization` in `mcp.json`.

**Option 2 — ChatGPT (web + desktop)** via Custom GPT + GPT Actions (requires ChatGPT Plus):

Create a Custom GPT and add an Action pointing at the Supabase Edge Functions — no local
install, no MCP config, works from both ChatGPT web and desktop. Uses the Supabase anon key
as Bearer auth.

**Option 3 — Local MCP (stdio or HTTP)** — requires Python + uv + local repo clone:

```bash
# stdio — for same-machine agents (Claude Desktop subprocess)
cerefox mcp

# HTTP — for remote agents over the network
cerefox mcp --transport http --port 8001
```

Claude Desktop (stdio):
```json
{
  "mcpServers": {
    "cerefox": {
      "command": "uv",
      "args": ["--directory", "/path/to/cerefox", "run", "cerefox", "mcp"]
    }
  }
}
```

Claude Code (HTTP):
```bash
claude mcp add cerefox --transport http http://YOUR_IP:8001/mcp
```

**Option 4 — JSON REST API** — for any HTTP client:

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "my search query", "match_count": 5}'
```

Full setup for all options: `docs/guides/connect-agents.md`

---

## Documentation

| Guide | Description |
|-------|-------------|
| `docs/guides/quickstart.md` | Zero to first document in 15 minutes |
| `docs/guides/setup-supabase.md` | Supabase project setup |
| `docs/guides/configuration.md` | All configuration options |
| `docs/guides/connect-agents.md` | MCP agent integration |
| `docs/guides/setup-local.md` | Local Docker setup |
| `docs/guides/ops-scripts.md` | Backup, restore, migrate |
| `docs/guides/setup-cloud-run.md` | Google Cloud Run deployment |
| `docs/guides/operational-cost.md` | Cost breakdown for all deployment options |
| `docs/guides/contributing.md` | Adding embedders, converters, commands |

---

## License

Apache 2.0 — see LICENSE.

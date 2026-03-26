# Local Setup Guide

Run Cerefox entirely on your own machine — zero cloud dependencies.

The local stack uses:
- **Postgres + pgvector** (Docker) for storage and vector search
- **PostgREST** (Docker) as the REST API layer (required by supabase-py)
- **Ollama** for embeddings (no API key needed)
- **Cerefox** app + MCP HTTP server (Docker)

---

## Prerequisites

- Docker / Podman with Compose
- [Ollama](https://ollama.com) installed and running (can be on the same machine or a remote host)

---

## Quick Start (Docker Compose)

```bash
# 1. Clone the repo
git clone https://github.com/kprojectgr/cerefox.git
cd cerefox

# 2. Pull the embedding model on your Ollama host
ollama pull nomic-embed-text

# 3. Create .env from the local template
cp .env.local.example .env
# Edit .env — set CEREFOX_OLLAMA_BASE_URL if Ollama is on a different machine
#   e.g. CEREFOX_OLLAMA_BASE_URL=http://192.168.0.8:11434

# 4. Generate the JWT for PostgREST auth
python scripts/generate_jwt.py
# Copy the output into .env as CEREFOX_SUPABASE_KEY=...

# 5. Build and start everything
docker compose -f docker-compose.local.yml up -d --build

# 6. Deploy the database schema (first time only)
docker compose -f docker-compose.local.yml exec cerefox python scripts/db_deploy.py --reset
# Type 'yes' when prompted

# 7. Open the web UI
# http://localhost:9100
```

That's it. You have:

| Service | URL | What it does |
|---------|-----|-------------|
| Web UI + JSON API | `http://localhost:9100` | Browse, search, ingest documents |
| MCP HTTP (agents) | `http://localhost:9101/mcp` | Remote MCP for Claude Code, Cursor, etc. |

---

## What's in the Stack

`docker-compose.local.yml` runs 4 containers:

| Container | Image | Purpose |
|-----------|-------|---------|
| `cerefox-postgres` | `pgvector/pgvector:pg16` | Database with vector extension |
| `cerefox-postgrest` | `postgrest/postgrest:v12` | REST API over Postgres (supabase-py talks to this) |
| `cerefox-app` | built from `Dockerfile` | Web UI + JSON API (port 9100) |
| `cerefox-mcp` | built from `Dockerfile` | MCP Streamable HTTP server (port 9101) |

All secrets are in `.env` (gitignored). See `.env.local.example` for all variables.

---

## Configuration

Edit `.env` to change settings. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CEREFOX_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL. Change if Ollama is on another machine. |
| `CEREFOX_OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Must output 768-dim vectors. |
| `CEREFOX_API_TOKEN` | (empty) | If set, `/api/*` and MCP HTTP require `Authorization: Bearer <token>`. |
| `POSTGRES_PASSWORD` | `cerefox` | Change for non-localhost deployments. |
| `PGRST_JWT_SECRET` | (see example) | JWT secret for PostgREST. Must be 32+ chars. |
| `CEREFOX_SUPABASE_KEY` | (generated) | JWT token. Run `python scripts/generate_jwt.py` to create. |

See `configuration.md` for the full reference.

---

## Ingesting Documents

Via the web UI at `http://localhost:9100` — paste content or upload files.

Via the JSON API:
```bash
curl -X POST http://localhost:9100/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"title": "My Note", "content": "# Hello\nSome content here", "project_name": "notes"}'
```

---

## Searching

Via the web UI search page.

Via the JSON API:
```bash
curl -X POST http://localhost:9100/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "my search query", "match_count": 5}'
```

---

## Connecting AI Agents

### MCP HTTP (Claude Code, Cursor)

The MCP server is already running at `http://localhost:9101/mcp`.

Claude Code:
```bash
claude mcp add cerefox --transport http http://YOUR_IP:9101/mcp
```

Cursor (`mcp.json`):
```json
{
  "mcpServers": {
    "cerefox": {
      "url": "http://YOUR_IP:9101/mcp"
    }
  }
}
```

Claude Desktop (via supergateway):
```json
{
  "mcpServers": {
    "cerefox": {
      "command": "npx",
      "args": ["-y", "supergateway", "--streamableHttp", "http://YOUR_IP:9101/mcp"]
    }
  }
}
```

See `connect-agents.md` for all client configurations.

---

## Stopping and Resetting

```bash
# Stop (keep data)
docker compose -f docker-compose.local.yml down

# Stop and delete all data (database, backups)
docker compose -f docker-compose.local.yml down -v

# View logs
docker compose -f docker-compose.local.yml logs -f cerefox
docker compose -f docker-compose.local.yml logs -f cerefox-postgrest
```

---

## Updating the Schema

When a new version introduces schema changes:

```bash
docker compose -f docker-compose.local.yml exec cerefox python scripts/db_migrate.py
```

Always back up first (see `ops-scripts.md`).

---

## Troubleshooting

**"Supabase is not configured" error**
Your `.env` is missing `CEREFOX_SUPABASE_KEY` or it doesn't match `PGRST_JWT_SECRET`. Regenerate the JWT — see `.env.local.example` for instructions.

**"JWSInvalidSignature" error**
The JWT in `CEREFOX_SUPABASE_KEY` doesn't match `PGRST_JWT_SECRET`. Regenerate it.

**PostgREST 404 or empty responses**
Run the schema deploy: `docker compose -f docker-compose.local.yml exec cerefox python scripts/db_deploy.py --reset`

**Port conflicts**
Edit the `ports:` mappings in `docker-compose.local.yml`. Only the host ports (left side of `:`) need to be unique.

**Ollama connection refused**
Check that Ollama is running and reachable from the Docker network. If Ollama is on the host machine, use `http://host.docker.internal:11434` (macOS/Windows) or your machine's LAN IP (Linux).

**pgvector extension not found**
Make sure you're using the `pgvector/pgvector:pg16` Docker image. Raw Postgres images don't include pgvector.

"""JSON REST API endpoints for programmatic access.

These are the local equivalents of the Supabase Edge Functions
(cerefox-search, cerefox-ingest, cerefox-metadata). Any HTTP client —
ChatGPT GPT Actions, curl, custom agents — can call these.

Routes:
    POST /api/search      Search the knowledge base (JSON in/out)
    POST /api/ingest      Ingest a document (JSON in/out)
    POST /api/metadata    List metadata keys (JSON in/out)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cerefox.api.routes import get_client, get_embedder, get_settings
from cerefox.config import Settings
from cerefox.db.client import CerefoxClient
from cerefox.embeddings.base import Embedder

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/api", tags=["api"])


# ── Auth dependency ──────────────────────────────────────────────────────────


def _check_api_token(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Verify bearer token if CEREFOX_API_TOKEN is configured."""
    token = settings.api_token
    if not token:
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


# ── Request/response models ──────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    project_name: str | None = None
    match_count: int = Field(default=5, ge=1, le=100)
    mode: str = Field(default="docs", pattern="^(hybrid|fts|semantic|docs)$")
    alpha: float = Field(default=0.7, ge=0.0, le=1.0)
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    max_bytes: int = Field(default=65000, ge=1000)


class IngestRequest(BaseModel):
    title: str
    content: str
    project_name: str | None = None
    source: str = "agent"
    metadata: dict[str, Any] = Field(default_factory=dict)
    update_if_exists: bool = False


# ── Endpoints ────────────────────────────────────────────────────────────────


@api_router.post("/search")
def api_search(
    body: SearchRequest,
    _auth: None = Depends(_check_api_token),
    settings: Settings = Depends(get_settings),
    client: CerefoxClient = Depends(get_client),
    embedder: Embedder | None = Depends(get_embedder),
) -> dict:
    """Search the knowledge base. Returns JSON results."""
    # Resolve project name → ID
    project_id: str | None = None
    if body.project_name:
        projects = client.list_projects()
        for p in projects:
            if p["name"].lower() == body.project_name.lower():
                project_id = p["id"]
                break
        if project_id is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {body.project_name}")

    # FTS doesn't need an embedder
    if body.mode == "fts":
        rows = client.fts_search(
            query_text=body.query,
            match_count=body.match_count,
            project_id=project_id,
        )
        return _format_chunk_results(rows, body)

    if embedder is None:
        raise HTTPException(status_code=503, detail="Embedder not configured")

    embedding = embedder.embed(body.query)

    if body.mode == "docs":
        rows = client.search_docs(
            query_text=body.query,
            query_embedding=embedding,
            match_count=body.match_count,
            alpha=body.alpha,
            project_id=project_id,
            min_score=body.min_score,
        )
        return _format_doc_results(rows, body)

    if body.mode == "semantic":
        rows = client.semantic_search(
            query_embedding=embedding,
            match_count=body.match_count,
            project_id=project_id,
            min_score=body.min_score,
        )
        return _format_chunk_results(rows, body)

    # hybrid (default)
    rows = client.hybrid_search(
        query_text=body.query,
        query_embedding=embedding,
        match_count=body.match_count,
        alpha=body.alpha,
        project_id=project_id,
        min_score=body.min_score,
    )
    return _format_chunk_results(rows, body)


@api_router.post("/ingest", status_code=201)
def api_ingest(
    body: IngestRequest,
    _auth: None = Depends(_check_api_token),
    settings: Settings = Depends(get_settings),
    client: CerefoxClient = Depends(get_client),
    embedder: Embedder | None = Depends(get_embedder),
) -> dict:
    """Ingest a document into the knowledge base. Returns JSON result."""
    if embedder is None:
        raise HTTPException(status_code=503, detail="Embedder not configured")

    from cerefox.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(client, embedder, settings)
    result = pipeline.ingest_text(
        text=body.content,
        title=body.title,
        source=body.source,
        project_name=body.project_name,
        metadata=body.metadata,
        update_existing=body.update_if_exists,
    )

    resp = {
        "document_id": result.document_id,
        "title": result.title,
        "chunk_count": result.chunk_count,
        "total_chars": result.total_chars,
        "action": result.action,
    }
    if result.project_ids:
        resp["project_ids"] = result.project_ids
    return resp


@api_router.post("/metadata")
def api_metadata(
    _auth: None = Depends(_check_api_token),
    client: CerefoxClient = Depends(get_client),
) -> list[dict]:
    """List all metadata keys currently in use across documents."""
    return client.list_metadata_keys()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _format_doc_results(rows: list[dict], body: SearchRequest) -> dict:
    """Format document-level search results with byte budget."""
    results = []
    total_bytes = 0
    truncated = False
    for row in rows:
        block = {
            "doc_title": row.get("doc_title", ""),
            "best_score": row.get("best_score", 0),
            "full_content": row.get("full_content", ""),
        }
        block_bytes = len(str(block).encode())
        if total_bytes + block_bytes > body.max_bytes:
            truncated = True
            break
        results.append(block)
        total_bytes += block_bytes
    return {
        "results": results,
        "query": body.query,
        "mode": body.mode,
        "match_count": body.match_count,
        "truncated": truncated,
        "response_bytes": total_bytes,
    }


def _format_chunk_results(rows: list[dict], body: SearchRequest) -> dict:
    """Format chunk-level search results with byte budget."""
    results = []
    total_bytes = 0
    truncated = False
    for row in rows:
        block = {
            "chunk_id": row.get("chunk_id", ""),
            "document_id": row.get("document_id", ""),
            "title": row.get("title", ""),
            "content": row.get("content", ""),
            "heading_path": row.get("heading_path", []),
            "score": row.get("score", 0),
            "doc_title": row.get("doc_title", ""),
        }
        block_bytes = len(str(block).encode())
        if total_bytes + block_bytes > body.max_bytes:
            truncated = True
            break
        results.append(block)
        total_bytes += block_bytes
    return {
        "results": results,
        "query": body.query,
        "mode": body.mode,
        "match_count": body.match_count,
        "truncated": truncated,
        "response_bytes": total_bytes,
    }

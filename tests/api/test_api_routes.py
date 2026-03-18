"""Tests for the JSON REST API endpoints (/api/*).

Uses FastAPI's TestClient with dependency overrides — no real DB/embedder calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cerefox.api.app import create_app
from cerefox.api.routes import get_client, get_embedder, get_settings
from cerefox.config import Settings
from cerefox.ingestion.pipeline import IngestResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _mock_settings(api_token: str = "") -> Settings:
    with patch.dict(
        "os.environ",
        {
            "CEREFOX_EMBEDDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "CEREFOX_API_TOKEN": api_token,
        },
        clear=False,
    ):
        return Settings(supabase_url="http://fake", supabase_key="fake-key")


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.list_projects.return_value = [{"id": "proj-1", "name": "Work"}]
    client.search_docs.return_value = [
        {
            "doc_title": "Test Note",
            "best_score": 0.92,
            "full_content": "# Test Note\n\nSome content.",
        }
    ]
    client.hybrid_search.return_value = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "title": "Section",
            "content": "Matched content",
            "heading_path": ["Doc", "Section"],
            "score": 0.85,
            "doc_title": "Test Doc",
        }
    ]
    client.fts_search.return_value = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "title": "Section",
            "content": "FTS matched content",
            "heading_path": ["Doc"],
            "score": 0.5,
            "doc_title": "Test Doc",
        }
    ]
    client.semantic_search.return_value = []
    client.list_metadata_keys.return_value = [
        {"key": "tags", "doc_count": 3, "example_values": ["work", "personal"]}
    ]
    return client


@pytest.fixture()
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 768
    embedder.embed_batch.return_value = [[0.1] * 768]
    embedder.dimensions = 768
    embedder.model_name = "test-model"
    return embedder


@pytest.fixture()
def test_client(mock_client, mock_embedder) -> TestClient:
    app = create_app()
    settings = _mock_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_client] = lambda: mock_client
    app.dependency_overrides[get_embedder] = lambda: mock_embedder
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def authed_client(mock_client, mock_embedder) -> TestClient:
    """TestClient with API token auth enabled."""
    app = create_app()
    settings = _mock_settings(api_token="secret-token")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_client] = lambda: mock_client
    app.dependency_overrides[get_embedder] = lambda: mock_embedder
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Search ───────────────────────────────────────────────────────────────────


class TestApiSearch:
    def test_search_returns_json(self, test_client, mock_client) -> None:
        resp = test_client.post("/api/search", json={"query": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["query"] == "test"

    def test_search_docs_mode(self, test_client, mock_client) -> None:
        resp = test_client.post("/api/search", json={"query": "test", "mode": "docs"})
        assert resp.status_code == 200
        mock_client.search_docs.assert_called_once()

    def test_search_fts_mode_skips_embedding(self, test_client, mock_embedder) -> None:
        resp = test_client.post("/api/search", json={"query": "keyword", "mode": "fts"})
        assert resp.status_code == 200
        mock_embedder.embed.assert_not_called()

    def test_search_hybrid_mode(self, test_client, mock_client, mock_embedder) -> None:
        resp = test_client.post("/api/search", json={"query": "test", "mode": "hybrid"})
        assert resp.status_code == 200
        mock_embedder.embed.assert_called_once()
        mock_client.hybrid_search.assert_called_once()

    def test_search_semantic_mode(self, test_client, mock_client, mock_embedder) -> None:
        resp = test_client.post("/api/search", json={"query": "test", "mode": "semantic"})
        assert resp.status_code == 200
        mock_embedder.embed.assert_called_once()
        mock_client.semantic_search.assert_called_once()

    def test_search_with_project_filter(self, test_client, mock_client) -> None:
        resp = test_client.post(
            "/api/search", json={"query": "test", "project_name": "Work"}
        )
        assert resp.status_code == 200
        call_kwargs = mock_client.search_docs.call_args[1]
        assert call_kwargs["project_id"] == "proj-1"

    def test_search_unknown_project_404(self, test_client) -> None:
        resp = test_client.post(
            "/api/search", json={"query": "test", "project_name": "NoSuchProject"}
        )
        assert resp.status_code == 404

    def test_search_empty_results(self, test_client, mock_client) -> None:
        mock_client.search_docs.return_value = []
        resp = test_client.post("/api/search", json={"query": "nothing"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_missing_query_422(self, test_client) -> None:
        resp = test_client.post("/api/search", json={})
        assert resp.status_code == 422

    def test_search_no_embedder_fts_ok(self, mock_client) -> None:
        """FTS works even without an embedder."""
        app = create_app()
        settings = _mock_settings()
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_client] = lambda: mock_client
        app.dependency_overrides[get_embedder] = lambda: None
        client = TestClient(app)
        resp = client.post("/api/search", json={"query": "test", "mode": "fts"})
        assert resp.status_code == 200
        app.dependency_overrides.clear()

    def test_search_no_embedder_hybrid_503(self, mock_client) -> None:
        """Hybrid search fails gracefully without embedder."""
        app = create_app()
        settings = _mock_settings()
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_client] = lambda: mock_client
        app.dependency_overrides[get_embedder] = lambda: None
        client = TestClient(app)
        resp = client.post("/api/search", json={"query": "test", "mode": "hybrid"})
        assert resp.status_code == 503
        app.dependency_overrides.clear()


# ── Ingest ───────────────────────────────────────────────────────────────────


class TestApiIngest:
    def test_ingest_returns_json(self, test_client) -> None:
        with patch("cerefox.ingestion.pipeline.IngestionPipeline") as MockPipeline:
            MockPipeline.return_value.ingest_text.return_value = IngestResult(
                document_id="doc-1",
                title="My Note",
                chunk_count=2,
                total_chars=500,
                action="created",
                project_ids=[],
            )
            resp = test_client.post(
                "/api/ingest", json={"title": "My Note", "content": "# Hello\nWorld"}
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["document_id"] == "doc-1"
        assert data["action"] == "created"

    def test_ingest_skipped_action(self, test_client) -> None:
        with patch("cerefox.ingestion.pipeline.IngestionPipeline") as MockPipeline:
            MockPipeline.return_value.ingest_text.return_value = IngestResult(
                document_id="doc-1",
                title="Dupe",
                chunk_count=1,
                total_chars=100,
                action="skipped",
                project_ids=[],
            )
            resp = test_client.post(
                "/api/ingest", json={"title": "Dupe", "content": "same content"}
            )
        assert resp.status_code == 201
        assert resp.json()["action"] == "skipped"

    def test_ingest_with_project(self, test_client) -> None:
        with patch("cerefox.ingestion.pipeline.IngestionPipeline") as MockPipeline:
            MockPipeline.return_value.ingest_text.return_value = IngestResult(
                document_id="doc-1",
                title="Note",
                chunk_count=1,
                total_chars=50,
                action="created",
                project_ids=["proj-1"],
            )
            resp = test_client.post(
                "/api/ingest",
                json={"title": "Note", "content": "text", "project_name": "Work"},
            )
        assert resp.status_code == 201
        assert resp.json()["project_ids"] == ["proj-1"]

    def test_ingest_missing_title_422(self, test_client) -> None:
        resp = test_client.post("/api/ingest", json={"content": "no title"})
        assert resp.status_code == 422

    def test_ingest_missing_content_422(self, test_client) -> None:
        resp = test_client.post("/api/ingest", json={"title": "no content"})
        assert resp.status_code == 422

    def test_ingest_no_embedder_503(self, mock_client) -> None:
        app = create_app()
        settings = _mock_settings()
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_client] = lambda: mock_client
        app.dependency_overrides[get_embedder] = lambda: None
        client = TestClient(app)
        resp = client.post(
            "/api/ingest", json={"title": "Test", "content": "text"}
        )
        assert resp.status_code == 503
        app.dependency_overrides.clear()


# ── Metadata ─────────────────────────────────────────────────────────────────


class TestApiMetadata:
    def test_metadata_returns_json(self, test_client, mock_client) -> None:
        resp = test_client.post("/api/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["key"] == "tags"

    def test_metadata_empty(self, test_client, mock_client) -> None:
        mock_client.list_metadata_keys.return_value = []
        resp = test_client.post("/api/metadata")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Auth ─────────────────────────────────────────────────────────────────────


class TestApiAuth:
    def test_no_token_configured_allows_request(self, test_client) -> None:
        """When CEREFOX_API_TOKEN is empty, all requests are allowed."""
        resp = test_client.post("/api/metadata")
        assert resp.status_code == 200

    def test_valid_token_allows_request(self, authed_client) -> None:
        resp = authed_client.post(
            "/api/metadata", headers={"Authorization": "Bearer secret-token"}
        )
        assert resp.status_code == 200

    def test_invalid_token_rejects_request(self, authed_client) -> None:
        resp = authed_client.post(
            "/api/metadata", headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 401

    def test_missing_token_rejects_request(self, authed_client) -> None:
        resp = authed_client.post("/api/metadata")
        assert resp.status_code == 401

    def test_auth_applies_to_search(self, authed_client) -> None:
        resp = authed_client.post("/api/search", json={"query": "test"})
        assert resp.status_code == 401

    def test_auth_applies_to_ingest(self, authed_client) -> None:
        resp = authed_client.post(
            "/api/ingest", json={"title": "T", "content": "C"}
        )
        assert resp.status_code == 401

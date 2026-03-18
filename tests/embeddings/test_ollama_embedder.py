"""Tests for the Ollama embedder.

All tests use mocked httpx calls — no Ollama server needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cerefox.embeddings.base import Embedder


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ollama_response(embeddings: list[list[float]]) -> MagicMock:
    """Build a fake httpx response matching the Ollama /api/embed shape."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"embeddings": embeddings}
    return mock_resp


# ── Protocol ─────────────────────────────────────────────────────────────────


class TestOllamaEmbedderProtocol:
    def test_satisfies_embedder_protocol(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder()
        assert isinstance(embedder, Embedder)

    def test_dimensions_property(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder(dimensions=768)
        assert embedder.dimensions == 768

    def test_model_name_property(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder(model="nomic-embed-text")
        assert embedder.model_name == "nomic-embed-text"


# ── Init ─────────────────────────────────────────────────────────────────────


class TestOllamaEmbedderInit:
    def test_default_base_url(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder()
        assert embedder._base_url == "http://localhost:11434"

    def test_default_model(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder()
        assert embedder.model_name == "nomic-embed-text"

    def test_default_dimensions(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder()
        assert embedder.dimensions == 768

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder(base_url="http://localhost:11434/")
        assert not embedder._base_url.endswith("/")

    def test_custom_settings(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder

        embedder = OllamaEmbedder(
            base_url="http://gpu-box:11434",
            model="custom-model",
            dimensions=1024,
        )
        assert embedder._base_url == "http://gpu-box:11434"
        assert embedder.model_name == "custom-model"
        assert embedder.dimensions == 1024


# ── Embed (single text) ─────────────────────────────────────────────────────


class TestOllamaEmbed:
    @pytest.fixture()
    def embedder(self):
        from cerefox.embeddings.ollama import OllamaEmbedder

        return OllamaEmbedder()

    def test_embed_returns_768_floats(self, embedder) -> None:
        fake_vec = [0.1] * 768
        with patch("httpx.post", return_value=_make_ollama_response([fake_vec])):
            result = embedder.embed("hello world")
        assert isinstance(result, list)
        assert len(result) == 768

    def test_embed_posts_to_correct_url(self, embedder) -> None:
        fake_vec = [0.1] * 768
        with patch("httpx.post", return_value=_make_ollama_response([fake_vec])) as mock_post:
            embedder.embed("hello")
        called_url = mock_post.call_args[0][0]
        assert called_url == "http://localhost:11434/api/embed"

    def test_embed_sends_model_in_payload(self, embedder) -> None:
        with patch(
            "httpx.post", return_value=_make_ollama_response([[0.0] * 768])
        ) as mock_post:
            embedder.embed("my text")
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "nomic-embed-text"
        assert payload["input"] == ["my text"]

    def test_embed_raises_on_http_error(self, embedder) -> None:
        import httpx

        with patch("httpx.post", side_effect=httpx.HTTPError("connection refused")):
            with pytest.raises(RuntimeError, match="Ollama API request failed"):
                embedder.embed("boom")

    def test_embed_raises_on_status_error(self, embedder) -> None:
        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404, text="model not found"),
        )
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Ollama API error"):
                embedder.embed("bad model")


# ── Embed batch ──────────────────────────────────────────────────────────────


class TestOllamaEmbedBatch:
    @pytest.fixture()
    def embedder(self):
        from cerefox.embeddings.ollama import OllamaEmbedder

        return OllamaEmbedder()

    def test_embed_batch_empty_input_returns_empty(self, embedder) -> None:
        with patch("httpx.post") as mock_post:
            result = embedder.embed_batch([])
        assert result == []
        mock_post.assert_not_called()

    def test_embed_batch_returns_one_vector_per_input(self, embedder) -> None:
        vecs = [[float(i)] * 768 for i in range(3)]
        with patch("httpx.post", return_value=_make_ollama_response(vecs)):
            result = embedder.embed_batch(["a", "b", "c"])
        assert len(result) == 3

    def test_embed_batch_preserves_order(self, embedder) -> None:
        vecs = [[0.1] * 768, [0.2] * 768]
        with patch("httpx.post", return_value=_make_ollama_response(vecs)):
            result = embedder.embed_batch(["first", "second"])
        assert result[0][0] == pytest.approx(0.1)
        assert result[1][0] == pytest.approx(0.2)

    def test_embed_batch_splits_into_batches(self) -> None:
        from cerefox.embeddings.ollama import OllamaEmbedder, _BATCH_SIZE

        embedder = OllamaEmbedder()
        n = _BATCH_SIZE + 5
        texts = [f"text {i}" for i in range(n)]

        call_count = [0]

        def fake_post(*args, **kwargs):
            batch = kwargs["json"]["input"]
            call_count[0] += 1
            return _make_ollama_response([[0.0] * 768 for _ in batch])

        with patch("httpx.post", side_effect=fake_post):
            result = embedder.embed_batch(texts)

        assert call_count[0] == 2
        assert len(result) == n

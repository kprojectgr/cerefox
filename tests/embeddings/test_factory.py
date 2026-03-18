"""Tests for the embedder factory.

Verifies that create_embedder() returns the correct embedder type
based on settings, and raises on missing configuration.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cerefox.embeddings.base import Embedder
from cerefox.embeddings.factory import create_embedder


def _make_settings(**overrides):
    """Create a Settings instance with test defaults."""
    from cerefox.config import Settings

    defaults = {
        "CEREFOX_SUPABASE_URL": "https://test.supabase.co",
        "CEREFOX_SUPABASE_KEY": "test-key",
        "OPENAI_API_KEY": "test-openai-key",
        "CEREFOX_FIREWORKS_API_KEY": "test-fw-key",
    }
    defaults.update(overrides)
    with patch.dict("os.environ", defaults, clear=False):
        return Settings()


class TestCreateEmbedder:
    def test_openai_returns_cloud_embedder(self) -> None:
        settings = _make_settings(CEREFOX_EMBEDDER="openai")
        embedder = create_embedder(settings)

        from cerefox.embeddings.cloud import CloudEmbedder

        assert isinstance(embedder, CloudEmbedder)
        assert isinstance(embedder, Embedder)

    def test_fireworks_returns_cloud_embedder(self) -> None:
        settings = _make_settings(CEREFOX_EMBEDDER="fireworks")
        embedder = create_embedder(settings)

        from cerefox.embeddings.cloud import CloudEmbedder

        assert isinstance(embedder, CloudEmbedder)
        assert embedder.model_name == "nomic-ai/nomic-embed-text-v1.5"

    def test_ollama_returns_ollama_embedder(self) -> None:
        settings = _make_settings(CEREFOX_EMBEDDER="ollama")
        embedder = create_embedder(settings)

        from cerefox.embeddings.ollama import OllamaEmbedder

        assert isinstance(embedder, OllamaEmbedder)
        assert isinstance(embedder, Embedder)
        assert embedder.model_name == "nomic-embed-text"

    def test_openai_missing_key_raises(self) -> None:
        settings = _make_settings(CEREFOX_EMBEDDER="openai", OPENAI_API_KEY="")
        with pytest.raises(ValueError, match="API key not set"):
            create_embedder(settings)

    def test_fireworks_missing_key_raises(self) -> None:
        settings = _make_settings(
            CEREFOX_EMBEDDER="fireworks", CEREFOX_FIREWORKS_API_KEY=""
        )
        with pytest.raises(ValueError, match="API key not set"):
            create_embedder(settings)

    def test_ollama_no_key_needed(self) -> None:
        settings = _make_settings(CEREFOX_EMBEDDER="ollama", OPENAI_API_KEY="")
        embedder = create_embedder(settings)
        assert embedder is not None

    def test_ollama_custom_base_url(self) -> None:
        settings = _make_settings(
            CEREFOX_EMBEDDER="ollama",
            CEREFOX_OLLAMA_BASE_URL="http://gpu-box:11434",
        )
        embedder = create_embedder(settings)
        assert embedder._base_url == "http://gpu-box:11434"

    def test_ollama_custom_model(self) -> None:
        settings = _make_settings(
            CEREFOX_EMBEDDER="ollama",
            CEREFOX_OLLAMA_EMBEDDING_MODEL="snowflake-arctic-embed:335m",
        )
        embedder = create_embedder(settings)
        assert embedder.model_name == "snowflake-arctic-embed:335m"

"""Ollama embedder — local embedding via Ollama's REST API.

Requires Ollama running locally (or on a reachable host). No API key needed.

Default model: nomic-embed-text (768 dimensions, 8192 token context).
Install with: ollama pull nomic-embed-text
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_DIMENSIONS = 768
_BATCH_SIZE = 64  # Ollama handles batches natively; keep moderate to avoid OOM


class OllamaEmbedder:
    """Embedder backed by a local Ollama server.

    Args:
        base_url:   Ollama server URL (default: ``http://localhost:11434``).
        model:      Embedding model name (default: ``nomic-embed-text``).
        dimensions: Expected output vector dimensions (default: 768).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        dimensions: int = _DEFAULT_DIMENSIONS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions

    # ── Protocol properties ────────────────────────────────────────────────

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model

    # ── Public interface ───────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """Embed a single string and return a normalised float vector."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of strings in batches.

        Returns one vector per input, in the same order.
        Empty input returns an empty list.
        """
        if not texts:
            return []

        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            results.extend(self._call_api(batch))
        return results

    # ── Internal ───────────────────────────────────────────────────────────

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """POST to /api/embed and return the embedding vectors in input order."""
        try:
            response = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
                timeout=120.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama API request failed: {exc}") from exc

        embeddings = response.json()["embeddings"]
        return embeddings

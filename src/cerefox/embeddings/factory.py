"""Embedder factory — create the correct embedder from settings.

Centralises embedder instantiation so that cli.py, routes.py, and
mcp_server.py don't duplicate the same branching logic.
"""

from __future__ import annotations

from cerefox.config import Settings
from cerefox.embeddings.base import Embedder


def create_embedder(settings: Settings) -> Embedder:
    """Create and return the embedder specified by ``settings.embedder``.

    Raises:
        ValueError: If required configuration (e.g. API key) is missing.
    """
    if settings.embedder == "ollama":
        from cerefox.embeddings.ollama import OllamaEmbedder

        return OllamaEmbedder(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        )

    # Cloud embedders (openai, fireworks) share CloudEmbedder
    from cerefox.embeddings.cloud import CloudEmbedder

    api_key = settings.get_embedder_api_key()
    if not api_key:
        provider = settings.embedder.upper()
        raise ValueError(
            f"Embedding API key not set for {provider}. "
            f"Set the appropriate API key in your .env file."
        )
    return CloudEmbedder(
        api_key=api_key,
        base_url=settings.get_embedder_base_url(),
        model=settings.get_embedder_model(),
        dimensions=settings.get_embedder_dimensions(),
    )

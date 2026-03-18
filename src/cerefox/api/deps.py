"""Shared FastAPI dependency injection functions for the JSON API."""

from __future__ import annotations

import logging
from functools import lru_cache

from cerefox.config import Settings
from cerefox.db.client import CerefoxClient
from cerefox.embeddings.base import Embedder

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _cached_client() -> CerefoxClient:
    return CerefoxClient(_cached_settings())


@lru_cache(maxsize=1)
def _cached_embedder() -> Embedder | None:
    """Return the configured embedder, or None if configuration is incomplete."""
    settings = _cached_settings()
    try:
        from cerefox.embeddings.factory import create_embedder

        return create_embedder(settings)
    except Exception as exc:
        logger.warning("Embedder unavailable: %s", exc)
        return None


def get_settings() -> Settings:
    return _cached_settings()


def get_client() -> CerefoxClient:
    return _cached_client()


def get_embedder() -> Embedder | None:
    return _cached_embedder()

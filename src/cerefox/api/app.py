"""FastAPI application factory for the Cerefox web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cerefox.api.api_routes import api_router
from cerefox.api.routes import router

# Resolve paths relative to this file so they work regardless of cwd.
_PKG_ROOT = Path(__file__).parent.parent.parent.parent  # project root
TEMPLATES_DIR = _PKG_ROOT / "web" / "templates"
STATIC_DIR = _PKG_ROOT / "web" / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Cerefox",
        description="Personal knowledge base web UI",
        version="0.1.0",
    )

    # Jinja2 templates — attached to app.state so routes can access them.
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    # Mount static files if the directory exists.
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(router)
    app.include_router(api_router)
    return app


# Module-level app instance for uvicorn / CLI.
app = create_app()

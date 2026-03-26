# ── Frontend build stage ──────────────────────────────────────────────────────
FROM node:20-slim AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Python build stage ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv for fast dependency resolution.
RUN pip install --no-cache-dir uv

# Copy dependency manifests and README (required by hatchling) for layer caching.
COPY pyproject.toml README.md ./
COPY src/ src/

# Install dependencies into a virtual environment.
RUN uv sync --no-dev

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy the pre-built virtual environment and source.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Copy static assets and built SPA.
COPY web/static/ web/static/
COPY --from=frontend /app/frontend/dist/ frontend/dist/

# Copy scripts for schema deployment inside the container.
COPY scripts/ scripts/

# Activate the virtual environment.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

CMD ["uvicorn", "cerefox.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

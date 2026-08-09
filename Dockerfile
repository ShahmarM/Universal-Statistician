# Single-process deploy image for the FastAPI REST layer (api.py) — Phase K.
# Deliberately minimal, matching this project's stated scope (see plan.md):
# a personal/local tool, not a microservices deployment. No Kubernetes
# manifests, no Redis, no Postgres here — the catalog is a SQLite file
# (persist it with a volume mount at USTAT_CATALOG_DB_PATH) and rate
# limiting is in-process (see api.py's _RateLimiter docstring for why that's
# an intentional, documented single-process limitation, not an oversight).

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Catalog persistence (Phase B): mount a volume here, or set
# USTAT_CATALOG_DB_PATH to point elsewhere. Startup never requires this
# path to already exist or be reachable over a network — see
# core/engine.py::_open_catalog().
VOLUME ["/data"]
ENV USTAT_CATALOG_DB_PATH=/data/catalog.db

EXPOSE 8000

# See README's "Переменные окружения" / Configuration section for every
# supported env var (ANTHROPIC_API_KEY, CENSUS_API_KEY, USTAT_CATALOG_DB_PATH,
# USTAT_CORS_ORIGINS, USTAT_RATE_LIMIT_REQUESTS, USTAT_RATE_LIMIT_WINDOW_SECONDS,
# USTAT_UPSTREAM_TIMEOUT_SECONDS) -- none are baked into this image; set them
# with `docker run -e ...` or your platform's secret/env mechanism. Never
# bake an API key into the image itself.
CMD ["uvicorn", "universal_statistician.api:app", "--host", "0.0.0.0", "--port", "8000"]

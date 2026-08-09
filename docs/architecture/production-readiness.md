# Phase K: production readiness

What this phase covers, per the task's explicit list, and what it
deliberately doesn't (no Kubernetes, no Redis, no Postgres — this
project's stated scope is a personal/local tool, not a multi-tenant
service, and nothing here needed a bigger dependency to satisfy).

## `/health`

`GET /health` (`api.py`) returns `{"status": "ok", "catalog": {...}}`.
Deliberately touches **only** the local SQLite catalog (`Catalog.
summary()`, already used by `ustat catalog stats`) — never a provider's
upstream API. Live-verified: with `USTAT_RATE_LIMIT_REQUESTS=2` set,
three real `curl` requests against a real running `uvicorn` process
returned `200`, `200`, `429` in order — both `/health` itself and the
rate limiter (below) work end to end, not just through `TestClient`.
Offline regression: `test_health_reports_ok_and_catalog_stats_without_
touching_any_provider` replaces every registered provider with one that
raises on any call and confirms `/health` still returns `200`.

## CORS via environment variable

Previously hardcoded to `http://(localhost|127\.0\.0\.1)(:\d+)?` (the
project's original personal/local-tool default, since the dashboard may
be served by Vite's dev server or as static files on whatever port
either picks). `USTAT_CORS_ORIGINS` (a comma-separated explicit origin
list) now overrides that for a real deployment; unset keeps the exact
original behavior — no accidental widening to "allow everything."

## Upstream timeouts

Every provider that talks to an external API already had a *default*
timeout before this phase: `sdmx1`'s own `Session` class defaults to
30s, `pxweb.PxApi` defaults to 30s, and `CensusProvider`/
`worldbank_discovery.py` already passed `timeout=30` explicitly. None of
them could hang forever even before Phase K — verified by reading each
dependency's own source rather than assumed. What was missing was a way
to *tune* that number for a real deployment without editing source: one
shared function, `providers/http_config.py::upstream_timeout_seconds()`
(reads `USTAT_UPSTREAM_TIMEOUT_SECONDS`, defaults to 30.0), now used
consistently by `SDMXProvider` (passed to `sdmx.Client`), `PXWebProvider`
(passed to `PxApi`), and `CensusProvider` (passed to each `requests.get`)
instead of three independently hardcoded `30`s.

## Basic rate limiting

`api.py::_RateLimiter` — a fixed-window counter per client IP, behind a
`threading.Lock` (Starlette runs sync work in a thread pool, the same
concurrency concern this project's `Catalog`/`Cache` already had to
handle). Configurable via `USTAT_RATE_LIMIT_REQUESTS` (default `0` =
disabled — a personal/local tool with no untrusted traffic doesn't need
this on by default) and `USTAT_RATE_LIMIT_WINDOW_SECONDS` (default
`60`). **Explicitly documented as single-process only** — it would need
a shared backend (Redis or similar) to work correctly across multiple
worker processes or replicas, which this project's scope doesn't call
for; said plainly here rather than silently giving a false sense of
protection in a scaled-out deployment.

## GitHub Actions CI

`.github/workflows/ci.yml` — installs the package with dev extras and
runs `pytest -q -m "not network"` on every push/PR. Deliberately excludes
the `network`-marked tests: they hit real official statistics APIs, and
making CI depend on hosts this project doesn't control (rate limits,
outages — see Phase H's own live 502 findings) would make CI flaky for
reasons unrelated to code correctness. Live verification stays a
manual/local step (`pytest -m network`), exactly as this project has
done throughout the live-verification rounds.

## Simple deploy config

`Dockerfile` (single-stage, `python:3.11-slim`, installs the package,
runs `uvicorn universal_statistician.api:app`) + `.dockerignore`. A
`VOLUME` at `/data` for the SQLite catalog file
(`USTAT_CATALOG_DB_PATH=/data/catalog.db` set as the image default, so
catalog data survives container restarts when that volume is mounted —
consistent with Phase B's persistence requirement). No image bakes in
any API key; every credential is read from the environment at runtime
(`docker run -e ANTHROPIC_API_KEY=... -e CENSUS_API_KEY=...`).

## Startup never requires upstream APIs

Already true before this phase, confirmed rather than assumed:
- `SDMXProvider.__init__` only constructs `sdmx.Client(...)` — no request
  is made until `get_series()`/`discover_catalog_entries()` is actually
  called (`sdmx.Client()` connects lazily, per the library's own design).
- `PXWebProvider.__init__` deliberately does **not** construct `PxApi(...)`
  eagerly — its own docstring explains why: `PxApi.__init__` makes an
  eager network call, so building it at `default_engine()` construction
  time would break app startup entirely if any one PX-Web agency were
  unreachable. `_api()` builds it lazily, on first real use.
- `core/engine.py::_open_catalog()` opens the local SQLite file directly
  (or seeds an in-memory one for `USTAT_CATALOG_DB_PATH=":memory:"`) —
  no network call.

`/health`'s own implementation is the concrete proof: it succeeds even
when every provider is replaced with one that always raises (see the
offline test above), which could only be true if constructing the engine
itself never touched any upstream host.

## Documented environment variables

Every environment variable this project reads, in one place — see
README's "Переменные окружения" section (mirrors this list):

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Enables `AnthropicPlanner` for `ustat chat`/`ustat plan --llm`/`/ask`&`/plan` with `use_llm: true` | unset — falls back to `RuleBasedPlanner` |
| `CENSUS_API_KEY` | Required for US Census retrieval (discovery works without it) | unset — `get_series()` raises `CensusMissingApiKeyError` |
| `USTAT_CATALOG_DB_PATH` | Where the persistent SQLite catalog lives | `~/.universal_statistician/catalog.db` (`:memory:` opts out of persistence) |
| `USTAT_CORS_ORIGINS` | Comma-separated allowed origins for the REST API | unset — any `localhost`/`127.0.0.1` origin |
| `USTAT_RATE_LIMIT_REQUESTS` | Max requests per client IP per window (`0` disables) | `0` (disabled) |
| `USTAT_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window length | `60` |
| `USTAT_UPSTREAM_TIMEOUT_SECONDS` | Timeout for every request to an official statistics API | `30` |

**Never commit an API key** — every credential above is read from the
environment at runtime, none is ever written to a file in this
repository (checked before every commit in this session that touched a
real key — see the live-verification rounds' summaries).

## Offline tests

`tests/test_api.py` (`/health`, CORS env-var resolution, `_RateLimiter`
unit tests, and a `TestClient`-driven 429 check) and
`tests/test_http_config.py` (the shared timeout default/override). **313
offline tests** (was 303 before this phase).

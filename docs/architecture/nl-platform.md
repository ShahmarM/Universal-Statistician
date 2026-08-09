# From MVP to a universal official-statistics platform

This documents the expansion beyond the original MVP (`plan.md`) toward two
capabilities: **large-scale source expansion** and **natural-language
statistical analysis**, while preserving every existing interface (MCP, CLI,
REST API, dashboard, chat) and the core principle:

> Universal Statistician answers statistical questions using official
> sources, can derive new statistical series from official data, and must
> preserve complete provenance from every final result back to the
> underlying official observations.

Work proceeds in phases, each with its own tests and commit — see the phase
table below for current status. This file is updated after every phase.

## Target architecture

```
User question
  |
  v
LLM/query planner        (Phase 7 — not yet built)
  |
  v
Catalog search            core/catalog.py — implemented, being extended
  |
  v
Query plan                 (Phase 7 — not yet built)
  |
  v
Provider selection          core/engine.py — implemented (manual); ranking is Phase 8
  |
  v
Official APIs                providers/*_provider.py
  |
  v
Normalized observations      core/models.py: SeriesResult / Observation / Attribution
  |
  v
Calculation engine            core/compose.py — implemented; expansion is Phase 9
  |
  v
Validation                     (Phase 9 — not yet built)
  |
  v
Answer builder                 (Phase 11 — not yet built)
  |
  v
Chart + table + citations      (Phases 11-12 — not yet built)
```

Every interface (MCP server, CLI, REST API, dashboard, chat) sits as a thin
layer over `tools.py`, which calls `QueryEngine`. That does not change as
this expands — a `/ask` endpoint (Phase 11) is another thin layer over the
same core, not a parallel system.

## Provider architecture

Unchanged contract (`providers/base.py`):

```python
class Provider(ABC):
    def get_series(self, indicator_id, ref_area, *, start_period=None, end_period=None) -> SeriesResult: ...
    def describe(self) -> dict: ...
```

New, **optional** capability, added in Phase 1:

```python
class MetadataDiscoverable(Protocol):
    def discover_catalog_entries(self) -> list[IndicatorEntry]: ...
```

A `Protocol` (structural typing), not a required base class: a provider
gains catalog-discovery ability by adding one method, without changing what
it already is. Providers that don't implement it keep relying on manually
seeded catalog entries (`providers/catalog_seed.py`) — nothing about the
engine, the interfaces, or existing providers requires every source to
support discovery. This is what lets Phase 2 (World Bank discovery) through
Phase 6 (national statistical offices) be added one provider at a time
without breaking anything already working.

### Adding a new official provider

1. Implement `Provider` (`get_series`, `describe`) against the source's
   official API — see `providers/sdmx_provider.py` (generic, config-driven
   for any SDMX source) or `providers/pxweb_provider.py` (PX-Web) as
   references. Prefer a generic, config-driven provider over one class per
   source when the wire protocol is shared (most SDMX and PX-Web sources
   are).
2. If the source has a metadata/codelist/dataflow discovery API, implement
   `MetadataDiscoverable.discover_catalog_entries()` returning normalized
   `IndicatorEntry` objects. If it doesn't, or discovery can't be verified
   against a trustworthy source (the project's standing rule: no live
   network access in this environment, so verify against the client
   library's own test suite the way `providers/registry.py` and
   `providers/pxweb_registry.py` already do — never guess a key format or
   scrape an undocumented endpoint), document the gap instead, the way
   `README.md`'s "Почему нет Росстат/ЕМИСС" section does for a source with
   no reliable API at all.
3. Register the provider in `core/engine.py::default_engine()`, keeping
   construction network-free (see `PXWebProvider`'s lazy `_api()` for why —
   `default_engine()` runs at startup for every interface).
4. Seed at least one verified indicator in `catalog_seed.py` even if
   discovery isn't implemented yet, so the source is searchable immediately.

## Catalog architecture

`core/catalog.py`'s `Catalog` now backs search with two SQLite structures:

- `indicators` — the original FTS5 virtual table (multilingual full-text
  index), extended with more searchable columns (`keywords`, `unit`,
  `source_organization`, `geo`) per this phase's search requirement (search
  must cover units/dimensions/geography, not just name/description).
- `catalog_meta` — a plain table keyed by `(source_id, indicator_id)`,
  holding the metadata FTS5 can't natively upsert: `dataset_id`, `unit`,
  `frequency`, `geographic_coverage`, `dimensions`, `source_organization`,
  `official_url`, `last_updated`, `keywords`, `ingested_at`. `Catalog.add()`
  upserts into both (delete-then-insert for the FTS rows, `INSERT OR
  REPLACE` for `catalog_meta`), so re-running ingestion for a source updates
  its existing entries instead of duplicating them.

`IndicatorMeta`/`IndicatorEntry` (`core/models.py`) carry a superset of what
any one source publishes — every field beyond `indicator_id`/`name`/
`source_id` is optional, so a manually seeded entry with just a code and a
label and a fully discovered entry with dimensions/units/coverage are both
valid, and existing seed data and tests are unaffected.

Both structures are plain SQLite; nothing here depends on SQLite-only syntax
beyond FTS5 itself, so a later PostgreSQL migration (full-text search via
`tsvector`, `catalog_meta` as an ordinary table) would replace this module's
internals without changing `IndicatorEntry`/`IndicatorMeta` or any caller —
per the requirement not to require PostgreSQL for this phase, but to keep
the migration path straightforward.

## Metadata ingestion pipeline

`core/ingestion.py`:

```
provider.discover_catalog_entries()
  -> compare each entry against catalog.get(source_id, indicator_id)
  -> classify: added / updated / unchanged
  -> catalog.add(entries)   (upsert)
  -> IngestionReport
```

`ingest_source(source_id, provider, catalog)` handles one source;
`refresh_all(providers, catalog)` runs it for every provider that implements
`MetadataDiscoverable`, skipping (not erroring on) providers that don't — the
expected steady state while sources are added one phase at a time.
`QueryEngine.refresh_catalog(source_id=None)` exposes this to every
interface; `ustat catalog refresh [SOURCE_ID]` / `ustat catalog stats` are
the CLI entry points. Ingestion is explicit and admin-triggered — never run
automatically by `get_series`/`search_indicator`/`compare`, so an ordinary
query never pays for a metadata discovery call it didn't ask for (see
`core/ingestion.py`'s module docstring).

A provider without discovery support produces a structured
`IngestionReport` with an explanatory error rather than raising — proven
with `providers.base.LookupProvider`-style fakes in `tests/test_ingestion.py`
before any real source implements discovery, the same "prove the pipeline
offline first" approach already used for `SDMXProvider`/`PXWebProvider`.

## Not yet built (tracked per-phase)

Query planning, ambiguity handling, source-selection ranking, the expanded
calculation/validation engine, the full derived-statistic provenance model,
`/ask`, and the natural-language frontend are Phases 7-13 — each will extend
this document with its own section once implemented, following the same
"extend, don't replace" principle applied in Phase 1.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Catalog architecture & metadata normalization | ✅ done |
| 2 | World Bank broad catalog discovery | not started |
| 3 | Generalized IMF provider/catalog | not started |
| 4 | Generalized Eurostat integration | not started |
| 5 | OECD as first-class provider | not started |
| 6 | National statistical office plugin architecture | not started |
| 7 | Structured query planner + NL interface | not started |
| 8 | Source/indicator selection ranking | not started |
| 9 | Calculation and validation engine | not started |
| 10 | Provenance/citation system | not started |
| 11 | `/ask` endpoint and structured answer model | not started |
| 12 | Natural-language frontend experience | not started |
| 13 | Benchmarks, integration tests, hardening | not started |

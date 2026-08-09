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
LLM/query planner        planning/ — RuleBasedPlanner + AnthropicPlanner (Phase 7)
  |
  v
Catalog search            core/catalog.py — implemented, being extended
  |
  v
Query plan                 core/query_plan.py: QueryPlan (Phase 7)
  |
  v
Provider selection          core/selection.py — deterministic, explainable ranking (Phase 8)
  |
  v
Official APIs                providers/*_provider.py
  |
  v
Normalized observations      core/models.py: SeriesResult / Observation / Attribution
  |
  v
Calculation engine            core/compose.py — with_cagr/with_index/with_moving_average/... (Phase 9)
  |
  v
Validation                     core/validation.py — PASS/WARNING/FAIL (Phase 9)
  |
  v
Answer builder                 core/ask.py::answer_question() + core/answer.py (Phase 11)
  |
  v
Chart + table + citations      core/answer.py's AskResult/ChartSpec (Phase 11); frontend rendering is Phase 12
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

## World Bank broad catalog discovery (Phase 2)

`WorldBankProvider` (`providers/worldbank_provider.py`) subclasses
`SDMXProvider` — `get_series()`/`describe()` are unchanged — and adds
`discover_catalog_entries()`, backed by `providers/worldbank_discovery.py`.

This calls a **different** World Bank API than `get_series()` does. Data
retrieval uses the SDMX API (`SDMXProvider`, verified against `sdmx1`'s own
integration test suite). Discovery uses World Bank's other official API, the
bespoke v2 REST endpoint `GET /v2/source/2/indicator` (source id 2 = World
Development Indicators — the same database the SDMX dataflow queries),
documented at
https://datahelpdesk.worldbank.org/knowledgebase/articles/898581. `sdmx1`'s
test suite has no verified example of an SDMX *structure* request (codelist/
dataflow) for this source, only `data` — so this deliberately doesn't guess
an SDMX-structure route for discovery, the same "no verified example, don't
guess" rule already applied to OECD.

Honesty check, consistent with every other source in this project: the v2
REST API's shape is real, published documentation, but this has **not** been
exercised against the live endpoint from this sandbox (network blocked).
Parsing (`parse_indicator`) and pagination (`fetch_pages`) are both unit
-tested offline — the pagination loop against a fake session, parsing
against a payload built from the documented shape — and one `network`-marked
test (`test_live_discovery_returns_a_large_indicator_set`) is left for a
machine with real internet access. A live attempt from this sandbox via
`ustat catalog refresh WB_WDI` was run and confirmed the request reaches the
correct URL and fails cleanly (reported in `IngestionReport.errors`, not a
crash) when the proxy blocks it — real evidence the wiring is correct end to
end, short of the response body itself.

`MetadataDiscoverable` is a structural `Protocol`, so only `WorldBankProvider`
instances satisfy `isinstance(x, MetadataDiscoverable)` — `SDMXProvider`
instances for IMF/Eurostat do not, correctly reflecting that those sources
don't have discovery yet (Phases 3-4), even though they share a base class.

## Generalized IMF integration (Phase 3)

`IMFProvider` (`providers/imf_provider.py`) also subclasses `SDMXProvider`,
but — unlike World Bank — IMF genuinely has a **verified SDMX structure
-discovery path**: `sdmx1`'s own integration test suite
(`TestIMF_DATA.endpoint_args`) declares `"structure": dict(resource_id=
"DSD_CPI")` and `"codelist": dict(resource_id="CL_COUNTRY")` as real,
exercised endpoints. So discovery here calls
`client.get("datastructure", resource_id="DSD_CPI")` — a genuine SDMX
structure request, not a bespoke REST API — and walks the returned DSD's
dimensions.

The DSD tells us the *codes* (e.g. `CP01`, `CP02`, ... every COICOP
category the CPI dataflow actually publishes) for whichever dimension turns
out to be "the indicator dimension" — but nothing in the DSD response is
labeled "this is the indicator dimension" by name. Rather than guess a
dimension ID string, discovery reuses the **same fact already verified**
for `get_series()`: `SDMXSourceConfig.key_dimensions`' `"{indicator}"`/
`"{ref_area}"` placeholder positions are defined (registry.py's own
docstring) to match that dimension's position in the DSD's declared order.
So discovery and retrieval stay consistent with each other *by
construction* — one verified fact reused twice, not two independently
guessed ones. If a DSD response doesn't have exactly as many non-time
dimensions as `key_dimensions` declares, `IMFDiscoveryError` is raised
(surfaced as a clean `IngestionReport` error) rather than silently mapping
the wrong dimension.

`SDMXSourceConfig` gained two fields to support this and any future
SDMX-structure-discoverable source: `registry_id` (this entry's own key in
`SOURCES` — needed because `IndicatorEntry.source_id` must be the registry
key `QueryEngine` looks providers up by, e.g. `"IMF_DATA_CPI"`, which is
*not* the same as `config.source_id` (`"IMF_DATA"`, the underlying SDMX
agency id also used in `Attribution.source_id` — a distinction that
pre-existed this phase and is now made explicit rather than implicit) and
`structure_id` (the DSD resource id, `None` for sources without one).

Discovery is unit-tested against a `StructureMessage` built from real
`sdmx.model.v21` classes (`DataStructureDefinition`/`Dimension`/`Codelist`/
`Item`) — the same in-memory-object approach `tests/conftest.py` already
uses for data messages — including a case where the dimension count doesn't
match `key_dimensions`, proving the mismatch is reported cleanly rather than
silently mis-mapped. A live attempt via `ustat catalog refresh IMF_DATA_CPI`
from this sandbox confirmed the request reaches
`.../datastructure/all/DSD_CPI/latest?references=all` — note `references=all`
was applied automatically by `sdmx1`'s own default for a specific
`datastructure` resource_id (its documented behavior, not a parameter this
project invented) — and fails cleanly against the network block.

## Generalized Eurostat integration (Phase 4)

`EurostatProvider` (`providers/eurostat_provider.py`) follows the exact
discovery path `sdmx1`'s own integration test
(`TestESTAT.test_ss_data` — the same test `registry.py` already cites for
this dataflow's key format) uses against the live API:

```python
dsd = client.dataflow(resource_id="NAMA_10_GDP").dataflow["NAMA_10_GDP"].structure
if dsd.is_external_reference:
    dsd = client.get(resource=dsd).structure[0]
```

That test's own comment documents a real, Eurostat-specific server quirk:
*"Even with `?references=all`, ESTAT returns a short message with the DSD as
an external reference. Query again to get its actual contents."* — a
dataflow response's `.structure` is only a stub reference, not the resolved
DSD, unlike IMF's direct structure request (Phase 3). This is a real,
maintainer-observed behavior copied from the library's own test, not
guessed.

Once resolved, turning the DSD into `IndicatorEntry` objects is **shared**
with `IMFProvider` via the new `providers/sdmx_discovery.py` module
(`entries_from_dsd()`) — both providers reuse the same "key_dimensions
placeholder position tells you which DSD dimension is the indicator/ref_area
one" logic (see Phase 3's section above), now factored out instead of
duplicated. `IMFProvider`'s own `IMFDiscoveryError` became an alias for the
shared `SDMXDiscoveryError` for backward compatibility.

Tested offline with a fake client reproducing the real
external-reference-then-resolve sequence (two real requests: `dataflow` stub,
then the follow-up `resource=` resolve) built from real `sdmx.model.v21`
classes, plus a case where the DSD resolves in one step (proving the
follow-up request is correctly skipped when unnecessary). A live attempt via
`ustat catalog refresh ESTAT_NAMA_10_GDP` from this sandbox confirmed the
first request reaches
`.../dataflow/ESTAT/NAMA_10_GDP/latest?references=descendants` (again,
`sdmx1`'s own default, not invented here) and fails cleanly against the
network block.

## OECD investigated, not integrated (Phase 5)

Phase 5 asked for OECD "as a first-class official provider," using the same
normalized `Provider`/`MetadataDiscoverable` interfaces already built for
World Bank/IMF/Eurostat. Re-investigated with the same ground-truth
standard as every other source.

**Correction, stated plainly rather than quietly fixed:** the first pass at
this investigation checked `sdmx.Client("OECD_JSON").source.supports` and
wrongly generalized its "everything except data/metadata is unsupported"
result to OECD as a whole. Checking the actual "OECD" source (not
"OECD_JSON") shows `datastructure`, `dataflow`, `codelist`, and
`conceptscheme` are each declared **supported** — only the generic combined
`structure` endpoint (a different, less-used SDMX resource type from the
`datastructure` endpoint `sdmx_discovery.py::entries_from_dsd()` actually
needs) is `False`. This project's own regression test
(`test_oecd_is_not_registered_pending_a_verified_discovery_example`) caught
the error before it shipped as a false claim — worth recording as an
example of the "verify, don't assert" discipline catching itself, not just
external sources.

The real, narrower reason OECD stays unregistered:

1. **Endpoint types are supported; a specific verified example is not.**
   `sdmx1`'s own test suite (`TestOECD.endpoint_args`) has exactly one real,
   network-exercised OECD query — `data`, `resource_id="DSD_MSTI@DF_MSTI"`,
   with **no key** (fetches the entire dataflow, not one series) — and
   nothing for `datastructure`/`dataflow`/`codelist` with a specific
   resource_id to copy. Unlike IMF (verified: `structure`,
   `resource_id="DSD_CPI"`, Phase 3) and Eurostat (verified: `NAMA_10_GDP`'s
   dataflow→structure resolution, Phase 4), there's no worked discovery
   example here. Calling `dataflow`/`datastructure` with a *guessed*
   resource_id — even one derived from the composite `"DSD_MSTI@DF_MSTI"`
   id, whose `@`-joined format itself isn't confirmed to mean what it looks
   like it means — would be exactly the guess this project refuses to ship.
2. **The one source with a genuinely verified *filtered* query, `OECD_JSON`**
   (`TestOECD_JSON`: `resource_id="ITF_GOODS_TRANSPORT",
   key=".T-CONT-RL-TEU+T-CONT-RL-TON"`), needs a non-generic client
   (`sdmx.source.oecd_json.Client`, not the plain `sdmx.Client` this
   project uses for every other SDMX source) because — per that module's
   own docstring — its legacy `stats.oecd.org` endpoint requires
   downgrading the SSL/TLS handshake to connect at all, which the library's
   maintainers explicitly document as disabling protection against
   man-in-the-middle attacks, adding: "use with caution." Not a trade this
   project makes for one narrow, discovery-incapable, legacy dataflow.

Verified with real, reproducible commands (see
`test_additional_sources.py`): `sdmx.Client("OECD").source.supports[...]`
for each specific resource type (not just OECD_JSON's blanket result); and
reading `sdmx/source/oecd_json.py`'s own `Client()` factory docstring for
the SSL warning.

**Not ruled out permanently** — the architecture doesn't need to change:
a documented worked structure-discovery example against the current
`sdmx.oecd.org` API (from OECD's own developer docs, or a live environment
able to inspect a dataflow's DSD directly) would unblock a real
`OECDProvider` the same way Eurostat and IMF were unblocked in Phases 3-4 —
a verified `SDMXSourceConfig` entry and a provider reusing
`sdmx_discovery.py::entries_from_dsd()` exactly like IMF and Eurostat do.
Full reasoning is in `providers/registry.py`'s module docstring, extended
for this phase.

## National statistical office plugin architecture (Phase 6)

Two things happened in this phase, deliberately different in kind:

**1. PX-Web discovery became generic, not per-agency.** Unlike SDMX — where
World Bank, IMF, and Eurostat each needed a genuinely different discovery
mechanism (Phases 2-4) — PX-Web's `PxApi.get_table_variables()` is *already*
identical across every agency running the protocol. It's the exact method
`get_series()` already calls to find the time dimension's label; discovery
just reads one more field (`category.label`, a code → label map) from the
same response shape, read directly from `pxweb`'s own implementation
(`pxweb/api.py`), not guessed. So `discover_catalog_entries()` was added
directly to `PXWebProvider` itself — every PX-Web source registered in
`pxweb_registry.py` gets discovery automatically, no per-agency subclass
needed. This is the concrete proof the plugin architecture *does*
generalize once a source's discovery mechanism is generic enough — SCB
(Statistics Sweden), already registered since the original MVP, now
discovers its real content codes instead of relying on the single
hand-seeded `000007SF` entry. A second PX-Web agency (Statistics Norway,
`ssb` — a real, library-recognized shorthand per `pxweb`'s own
`known_apis`) was **not** registered: no verified table id exists for it
the way `TAB6471` was verified for SCB (pxwebpy's own test suite), and
guessing one would be exactly the risk this project refuses to take. Adding
it later needs only one verified table id — no new code.

**2. `CensusProvider`** (`providers/census_provider.py`) adds a **third**,
deliberately different wire-protocol family: the US Census Bureau's plain
REST/JSON API (`api.census.gov`), unrelated to both SDMX and PX-Web/
JSON-stat. Two genuine protocol differences, handled explicitly:

- Census publishes **one dataset per year**, not one endpoint spanning a
  period range — `get_series()` therefore requires both `start_period` and
  `end_period` (raises otherwise) and issues one HTTP request per year.
- A year with no data for a variable/geography is a **404**, not an empty
  result — skipped explicitly (contributes no observation for that period)
  rather than failing the whole multi-year request; any other HTTP error
  still propagates.

Scoped to the American Community Survey 1-Year Estimates, whose
"detailed table" variable codes (e.g. `B01003_001E`, total population) have
been stable for over a decade — chosen over the Population Estimates
Program specifically because PEP's variable *names* have changed across
vintages, which this project's ground-truth standard won't paper over.
`discover_catalog_entries()` reads Census's own `variables.json` endpoint
(`{"variables": {"CODE": {"label": ..., "concept": ..., ...}}}`), excluding
geography/identity fields (`NAME`, `GEO_ID`, ...) that aren't statistics.

**Honesty check, same standard as every other source:** Census's API shape
is real, extremely stable, published documentation
(census.gov/data/developers), not independently verified against a live
call from this sandbox. Unlike SDMX/PX-Web sources, there's no bundled
client library whose own test suite could serve as ground truth here — the
confidence is "well-documented and essentially unchanged for over a decade,"
the same tier of evidence Phase 2 already accepted for World Bank's v2 REST
API, not the stronger "verified in a dependency's own CI-tested suite" tier
IMF/Eurostat/SCB have. Parsing is unit-tested against payloads built from
the documented shape; `network`-marked tests are left for a machine with
real access. Because `CATALOG_SEED` is explicitly documented (its own
module docstring) as containing only indicators "already verified end to
end in `get_series()`," no Census entry was added there — consistent with
that stated policy, not an oversight; `ustat catalog refresh
US_CENSUS_ACS1` is how its catalog gets populated once network access
confirms this works.

Live attempts from this sandbox confirmed both:
`ustat catalog refresh SCB_TAB6471` reaches
`.../api/v2/config?lang=en` (the discovery-specific `PxApi` instance's
requested language, correctly separate from `get_series()`'s unset-language
instance) and `ustat catalog refresh US_CENSUS_ACS1` reaches
`.../data/2022/acs/acs1/variables.json` — both fail cleanly against the
network block.

## Structured query planner + NL interface (Phase 7)

`core/query_plan.py` defines the explicit, inspectable plan object the
"expose query plans in developer/debug mode" requirement (section 10)
calls for: `QuestionInterpretation` (a planner's reading of a question —
concepts, geographies, periods, transformations, comparison shape, output
type, assumptions, clarification need) and `QueryPlan` (that interpretation
plus the catalog's actual candidate indicators, resolvable to JSON via
`as_dict()` for a future `/ask` response).

**The anti-hallucination mechanism (section 23) is structural, not a
prompt instruction:** `QuestionInterpretation` has no field for an
indicator code at all — a planner can only propose natural-language
`concepts`. `build_query_plan()` is the *only* place `CandidateIndicator`
objects get created, and it does so by calling
`QueryEngine.search_indicator()` — the same deterministic catalog search
every interface already uses — once per concept. An LLM literally cannot
put a fabricated code into a QueryPlan; there's nowhere in the data model
for one to go.

`planning/` (new top-level package, parallel to `providers/` — isolated
because it depends on an optional external LLM client, per section 22):

- `planning/base.py`: `LLMPlanner`, a structural `Protocol` (same reasoning
  as `MetadataDiscoverable`) — any object with `interpret(question) ->
  QuestionInterpretation` qualifies, no shared base class forced on
  planners as different as the two below.
- `planning/rule_based_planner.py`: `RuleBasedPlanner` — no LLM, no
  external dependency. Satisfies section 22's "the statistical platform
  should remain operational for structured/manual queries without an LLM":
  treats the whole question as one literal catalog search phrase and says
  so explicitly via `assumptions`, rather than pretending to understand
  natural language it can't.
- `planning/anthropic_planner.py`: `AnthropicPlanner` — wraps the Claude
  API via a **forced tool call** (`tool_choice={"type": "tool", "name":
  "propose_query_plan"}`, a real, verified Anthropic SDK parameter shape),
  not free text: the model can only respond by filling in the plan schema,
  which has no slot for a data value or a code. The system prompt encodes
  section 11's ambiguity rule directly: infer the conventional reading when
  confident, record it in `assumptions`, and only set
  `needs_clarification` when interpretations would materially change the
  result. Mirrors `chat.py`'s pattern exactly — client injected, testable
  with a fake client built from real `anthropic.types` objects, no
  `ANTHROPIC_API_KEY` needed for tests.

Exposed now, ahead of the full `/ask` endpoint (Phase 11), via `ustat plan
"<question>"` (rule-based by default; `--llm` for `AnthropicPlanner`) —
lets a plan be inspected before Phase 9's calculation/validation layer or
Phase 11's answer builder exist, matching the letter of "before retrieving
observations, the plan should be inspectable/debuggable." A live run
against the real catalog (`ustat plan "population"`) resolved a real
candidate (`SP_POP_TOTL`); a stricter phrase (`"population of
Afghanistan"`) honestly returned zero candidates rather than a false match
— the rule-based planner's real, expected limitation, not a bug.

Source/indicator **selection** among candidates (`QueryPlan.
selected_indicators`) is Phase 8; calculation/validation
(`QueryPlan.validation_notes`) is Phase 9 — both fields exist on `QueryPlan`
now, empty, because their shape is already specified by this task's own
target `/ask` response (section 10), and stabilizing it avoids a breaking
change to every caller once those phases land.

## Source/indicator selection ranking (Phase 8)

`core/selection.py::select_indicators(plan)` picks exactly one candidate
per concept from `QueryPlan.candidate_indicators`, deterministically and
explainably (section 12), populating `QueryPlan.selected_indicators`.

Deliberately pure — no engine/network dependency. By the time a plan
reaches here, `CandidateIndicator` already carries the catalog metadata
(`unit`, `frequency`, `geographic_coverage`) needed to score it — extended
onto `CandidateIndicator` this phase specifically for this (it's exactly
what `engine.search_indicator()` already returned in `build_query_plan()`,
just not previously kept).

Scoring criteria, each contributing an explicit, human-readable reason
recorded per selection (never just a bare number):

- **Name match quality** — the requested concept appearing in the
  indicator's name scores higher than a full-text match that isn't an
  exact substring.
- **Geographic coverage** — a candidate whose known coverage includes every
  requested geography scores higher than one that excludes them; a
  candidate with *no recorded coverage* (not yet discovered) is treated as
  neutral, never penalized the same way as a source that's confirmed *not*
  to cover the request — an important distinction between "unknown" and
  "no."
- **Frequency match** — a candidate matching a requested frequency scores
  higher than a mismatched one; again neutral when unknown.

Freshness/completeness (also listed in section 12) are **not** scored here:
evaluating them needs retrieved observations, not just catalog metadata,
and belongs with the validation layer (Phase 9), not selection.

**"Never silently mix incompatible series" (section 12) is enforced by
construction**, not a warning bolted on after the fact: selection always
picks exactly one source per concept — candidates for the same concept are
never merged — and if a multi-concept plan's selections end up spanning
different sources, that fact is appended to `assumptions` explicitly
(`"Selected indicators for different concepts come from different sources
(...) — verify unit/frequency compatibility before combining them"`) rather
than left implicit for a caller to discover only after combining them.

Wired into `tools.build_plan()` (and therefore `ustat plan`) right after
`build_query_plan()` — a live run against the real catalog
(`ustat plan "population"`) shows the full pipeline: catalog match →
selection reason → `selected_indicators`.

## Calculation and validation engine (Phase 9)

### Calculation: compose.py expanded, with lineage

`ComparisonColumn` gained `formula` (a short, human-readable description of
the calculation) and `input_series` (the column keys it came from) — this
is the "lineage" section 14/17 require, and it's what the earlier
UOSA-Bench assessment flagged as the single highest-priority gap
(`docs/benchmarks/uosa-bench-v1-assessment.md`). Every derived column
`with_growth`/`with_ratio`/`with_rank` and everything new below produces
now carries both fields — inspectable via `ComparisonTable.as_dict()`.

New transformations, each independently tested, each populating
formula/input_series: `with_absolute_change`, `with_pp_change` (percentage
-point change, distinct key/label from absolute change even though the
math is the same — the point is self-documenting intent), `with_cagr` and
`with_cumulative_growth` (single value over a range, recorded at the end
period — not a per-period series; both reject non-annual periods with a
clear `ValueError` rather than silently computing a wrong "annual" rate
from monthly/quarterly data), `with_index` (rebasing to 100 at a base
period), `with_moving_average`, `with_difference` (general two-column
subtraction), `with_share` and `with_per_capita` (percentage-of-total and
per-capita — both thin, clearly-named wrappers over the same ratio
mechanics as `with_ratio`, kept separate because they're distinct,
frequently-requested concepts per section 14), and `with_sum`/
`with_average`/`with_weighted_average` (cross-column aggregation for a
period, weights always caller-supplied per section 14's "weights
explicitly defined" — never inferred; a period is skipped, not
partial-summed, if any input column is missing a value that period).
`with_period_over_period_growth` is also new: identical math to
`with_growth`, under an honest, frequency-neutral name/label for callers
who want to avoid `with_growth`'s long-documented "always says YoY, even
for monthly data" nuance without breaking existing callers of the original.

`SeriesResult` gained a `unit` field (also flagged in the UOSA-Bench
assessment) and `ComparisonColumn` gained `unit`/`frequency`, populated
from the fetching `SeriesResult` in `compare_across_countries`/
`compare_across_indicators`. Honestly scoped: no provider in this project
currently extracts `unit` from its source's response (SDMX/PX-Web/Census
don't return it inline with observation values the way they return
frequency) — the field exists so validation has somewhere real to read
from, and so a provider that *can* supply it later doesn't need another
model change.

### Validation: core/validation.py

`validate_series(series)` and `validate_table(table, ...)` produce a
structured `ValidationResult` (`PASS`/`WARNING`/`FAIL` findings, each
naming the specific check and a human-readable message) — a `FAIL` sets
`ValidationResult.ok = False`, section 16's "a FAIL should prevent an
unsupported numerical answer," meant to gate the future answer builder
(Phase 11).

`validate_series()` runs on one freshly retrieved `SeriesResult`, before
periods flatten into a table's `(period, key) -> value` dict: duplicate
periods (`WARNING`), a NaN value that should have been normalized to `None`
(`FAIL` — a real bug class, not a data-quality note), an empty result
(`WARNING`).

`validate_table()` runs on a `ComparisonTable`, possibly post
-transformation: citations exist for every base column (`FAIL` if a base
column has no `Attribution` — this project's core principle, not just a
checklist item), unit/frequency consistency across base columns
(`WARNING`, and only when *both* compared values are actually known and
differ — an unknown unit is never treated as a contradiction, the same
"unknown isn't a no" principle `core/selection.py` already applies),
requested geographies/period-range actually covered (`WARNING`),
unexpected gaps in annual coverage (`WARNING`, conservatively scoped to
columns whose periods all parse as plain 4-digit years, so it never
misfires on monthly/quarterly data it can't reason about safely), and
derived-column lineage integrity — every `input_series` reference must
point at a column that actually exists in the table (`FAIL` if not: broken
lineage is worse than a warning) and every derived column must carry a
`formula` (`WARNING` if missing). "Impossible transformations" (also
section 16) are caught earlier, structurally, by the transformation
functions' own `ValueError`s (unknown baseline/column, missing base
period, non-annual CAGR) rather than re-checked here.

A live run chained `compare_across_countries`-shaped data through
`with_growth` then `validate_table(..., requested_geographies=("AFG",
"USA"), requested_start_period="2010")` and got real, correct findings: a
missing requested geography and a genuine annual-coverage gap — both
flagged by name, not silently absorbed.

## Provenance/citation system (Phase 10)

`core/provenance.py::resolve_provenance(table, column_key, period)` walks a
`ComparisonTable` cell — base or derived, at any depth of computation — into
a full, traceable chain: `ObservationProvenance` for a directly-retrieved
value (provider, organization, dataset, indicator/series id, geography,
period, value, unit, official URL, retrieval timestamp) or
`DerivedProvenance` for a computed one (formula, `"Calculated by Universal
Statistician"`, a calculation timestamp, and the provenance of every
input — recursively). A live run reproduces section 17's own example almost
verbatim: a ratio's provenance carries its formula and resolves both inputs
down to real `WB_WDI`/`NY_GDP_PCAP_CD` observations with source, dataset,
and retrieval time.

Two small, well-motivated extensions made this possible:

- `ComparisonColumn` gained explicit `indicator_id`/`ref_area` — previously
  only implicit in `key` (which means different things depending on
  comparison shape: a ref_area in `compare_across_countries`, an indicator
  id in `compare_across_indicators`), so provenance couldn't previously
  name both the way section 17's example does ("NY.GDP.PCAP.CD" +
  "Azerbaijan") without guessing which one `key` was.
- Recursion works through *any* depth — a rank-of-a-ratio or a
  difference-of-two-ratios (real, reachable via `with_difference`'s
  arbitrary column arguments) resolves all the way down to observations,
  not just one level.

**Honesty limit, stated in the resolver's own docstring rather than
hidden:** `input_series` (Phase 9) records *which columns* a derived value
came from, not *which periods* of those columns — exact for same-period
operations (ratio, share, per_capita, difference, index, rank, sum,
average, weighted_average), but genuinely ambiguous for operations that
span more than one period of the same input (`with_growth`'s
previous+current, `with_cagr`'s start+end, a moving average's whole
window). For those, the resolver does not guess a single period — it
attaches provenance for *every* period that input actually has a value,
with an explicit `note` explaining why, rather than presenting
specific-looking but potentially wrong period references as certain.

## /ask endpoint and structured answer model (Phase 11)

`core/answer.py` defines the response shape sections 18/20/21 describe:
`AskResult` (question, query_plan, answer text, table, chart, sources,
provenance, warnings, validation) and `ChartSpec` (chart_type/title/axes/
series/source_note — data only, no server-side rendering; the frontend,
Phase 12, draws it with the existing `recharts` setup already in the
dashboard).

`core/ask.py::answer_question(engine, question, planner=None)` is the
orchestration pipeline (section 9's target diagram, now fully wired):
interpret → plan → select → **retrieve** → **transform** → **validate** →
**answer + chart + citations**. Every step reuses an already-built module —
this file only adds the glue:

- **Retrieval**: for each selected indicator × requested geography, calls
  `QueryEngine.get_series()` and assembles a `ComparisonTable` via
  `build_comparison()` — column keys/labels follow the existing
  `compare_across_countries`/`compare_across_indicators` conventions when
  only one axis varies (indicator or geography), and combine both when a
  plan varies both at once. A retrieval failure for one indicator/area
  becomes a warning, not a crash — the rest of the table still builds.
- **Transformations**: `plan.transformations` (free-form strings from the
  planner) dispatch by name to the no-extra-argument compose.py functions
  (`growth`, `period_over_period_growth`, `absolute_change`, `pp_change`,
  `rank`, `cagr`, `cumulative_growth`). Transformations needing a
  caller-specified column/weights (ratio, share, per_capita, difference,
  index, sum, average, weighted_average) are **not** auto-dispatched here —
  an honestly scoped limit, noted as a warning rather than silently
  ignored; they stay directly callable from `compose.py` for now.
- **Validation**: `validate_table()` runs with the plan's requested
  geographies/period range, so /ask's validation is genuinely tied to what
  was asked, not just what came back.
- **Answer text is template-built from the validated table only** — never
  an LLM rewriting numbers (section 18): one line per column reporting its
  latest available period and value, plus any non-PASS validation findings
  appended verbatim. No question goes through a second LLM call to phrase
  the answer; the planner (if an LLM one is used) only ever produces the
  *interpretation*, before any number exists.
- **Provenance**: `resolve_provenance()` (Phase 10) resolved for every
  column's latest period.
- Three outcomes short-circuit before retrieval, each explicit rather than
  a generic error: `needs_clarification` (asks the question back, no
  retrieval attempted), no geography identified, and no catalog candidate
  selected — matching section 23's "if the requested statistic cannot be
  found, say so."

Exposed on all three interfaces: `POST /ask` and `POST /plan` (api.py, both
taking `{"question": ..., "use_llm": false}` — `use_llm` opts into
`AnthropicPlanner` using the *server's own* `ANTHROPIC_API_KEY` env var,
never a key in the request body; missing key is a clean `400`, not a
crash), `ustat ask`/`ustat plan --llm` (cli.py, same `_resolve_llm_planner`
helper shared with `plan`), and an `ask` MCP tool — deliberately using only
the deterministic `RuleBasedPlanner` in the MCP case, since an MCP-connected
host has already done the natural-language understanding to produce
`question`; a second internal LLM call there would be redundant with the
server's own stated design (mcp_server.py's docstring).

Live end-to-end runs confirmed all three surfaces: `ustat ask population`
resolves a real catalog candidate; `POST /ask {"question": "population"}`
returns 200 with the full structured shape (and honestly reports "no
geography identified" since the rule-based planner doesn't extract one);
`POST /ask {"use_llm": true}` without a server-side key returns a clean
`400`, not a crash.

## Natural-language frontend experience (Phase 12)

`frontend/src/components/AskTab.tsx` — a new "Спросить" (Ask) tab, first/
default in the dashboard's tab order (section 24's "primary interaction"),
built the same way every other tab is: `frontend/src/api.ts` gains typed
`ask()`/`buildPlan()` calls to `POST /ask`/`POST /plan`, `types.ts` gains
the matching TypeScript interfaces (`AskResult`, `QueryPlan`,
`ChartSpec`, `ValidationResult`, ...) mirroring the backend's `as_dict()`
shapes field-for-field. Existing tabs (Search/Series/Compare) are
unchanged.

Result view covers every element section 24 asks for: concise answer text,
a `recharts` line chart (same component/color convention `CompareTab.tsx`
already established) when a chart spec and table are present, the
structured table (derived columns marked `*`, same convention as
`CompareTab`), a sources/citation list, an assumptions list, a warnings
banner (new `.warning-banner` style, amber — distinct from the existing red
`.error-banner`, since a warning isn't a failure), a validation status
badge (`PASS`/`WARNING`/`FAIL`, colored) with an expandable findings list,
and two collapsible (`<details>`) debug sections: the full query plan JSON
and the resolved provenance JSON — both real, inspectable data, not a
summary.

Example suggestions are split honestly rather than presented as uniformly
usable: short phrases (`"population"`, `"gross domestic product"`) that the
default `RuleBasedPlanner` can actually resolve against the demo catalog,
and the richer natural-language examples from section 24 itself, labeled
as needing the "Использовать Claude" checkbox — a boolean opt-in into
`AnthropicPlanner` via `use_llm` in the request body (never a key typed
into the frontend; the key lives only in the server's environment, same
principle as `api.py`'s `_resolve_planner`).

Verified live (Playwright/Chromium, not just `tsc --noEmit`, per this
project's established practice of browser-checking UI work): asking
`"population"` against a real backend returns a real answer showing the
honest "no geography identified" limitation with its warning banner styled
correctly, the assumptions list populated from the real `QueryPlan`, and
the debug `<details>` block expanding to real JSON (`SP_POP_TOTL`/`WB_WDI`
candidate, no fabricated content); switching to the existing "Поиск" tab
afterward confirmed no regression from the new tab/CSS.

## Benchmarks, observability, and performance hardening (Phase 13)

### Benchmark-style natural-language tests (section 25)

`tests/test_benchmarks.py` runs the task description's own six example
questions through the real `answer_question()` pipeline — "What is the
population of Azerbaijan?", "Show Azerbaijan GDP from 2010 to 2025.",
"Compare inflation in Azerbaijan and Georgia.", "What was cumulative GDP
growth between 2015 and 2024?", "Rank EU countries by unemployment.",
"Show GDP per capita for Azerbaijan, Georgia and Armenia." No live LLM is
available in this sandbox, so each benchmark supplies a scripted
`QuestionInterpretation` standing in for what a working `AnthropicPlanner`
should produce (the same pattern `test_ask.py` already established) — this
is exactly what section 25 asks for: define the expected intent/indicator
family/geography/period/operation/output structure per benchmark, and
validate *structure, provenance, and series selection*, not exact live
numbers. Each test runs against a small, clearly-synthetic multi-country
catalog built for this file (`LookupProvider`, fake AZE/GEO/ARM/DEU/FRA/ITA
data — never presented as real).

Together the six prove every pipeline shape the target demonstration
(section 31) exercises: a single direct observation, an explicit period
range, a cross-country comparison, a derived single-value statistic with
traceable provenance (`with_cumulative_growth`, resolved via
`resolve_provenance`), a ranking, and a comparison of a directly-published
per-capita indicator (matching how World Bank actually publishes
`NY.GDP.PCAP.CD` as its own series — not a client-side computation from
GDP ÷ population, which `/ask` doesn't auto-dispatch, see Phase 11's
scoped limit).

### Observability (section 26)

`core/engine.py::QueryEngine.get_series()`/`search_indicator()` and
`core/ask.py::answer_question()` now emit structured log records (stdlib
`logging`, no new dependency — a personal/local tool doesn't need a metrics
pipeline, only records worth grepping/forwarding if one is added later):
`catalog_search` (query, limit, result count), `cache_hit`/`cache_miss`,
`provider_request_completed` (with retrieval time in ms),
`ask.question_received`, `ask.plan_built` (concepts, candidate count,
selected indicators), `ask.transformations_applied`, and `ask.completed`
(outcome, validation status, total elapsed ms). Nothing here ever logs a
secret — no code path touches `ANTHROPIC_API_KEY` or any credential, only
source/indicator/area identifiers and timings already public in this
project's own catalog. Verified with `caplog`
(`tests/test_observability.py`), not just present-in-source: real log
records with the expected fields, not a hope that logging calls are
reachable.

### Performance (section 27)

Already satisfied by the existing architecture from earlier phases — this
phase adds regression tests confirming it, rather than new code:
`Cache.make_key()` (Phase 1-era) already includes every relevant query
dimension (source, indicator, area, start/end period); metadata ingestion
has never been automatic (`core/ingestion.py`'s module docstring, Phase 1)
and stays admin-triggered only (`ustat catalog refresh`); `default_engine()`
constructs every provider network-free (Phases 2-6 each fixed exactly this
class of bug — see e.g. `PXWebProvider`'s docstring). New tests:
`test_default_engine_construction_does_not_touch_the_network` (a real
construction in this network-blocked sandbox — any real attempt would
raise, not silently pass) and
`test_get_series_never_triggers_catalog_ingestion_automatically` (a
provider tracking whether its own discovery method was ever called during
ordinary retrieval/search — proven zero calls).

## What's genuinely not done

Every phase in the original 13-phase plan has landed except OECD (Phase 5
— investigated twice, correctly not integrated; see that section above for
exactly what would unblock it). Real, honest limits that remain, tracked
here rather than left implicit:

- **`/ask`'s automatic transformation dispatch is partial** (Phase 11):
  `ratio`/`share`/`per_capita`/`difference`/`index`/`sum`/`average`/
  `weighted_average` all need a caller-specified column/weights a bare
  transformation name can't carry, so they aren't auto-applied from a
  `QueryPlan` — they stay directly callable from `compose.py`. A real
  next step, not attempted here: let the planner propose which existing
  *column* a transformation should reference (not just its name).
- **Provenance can't pin an exact contributing period for multi-period
  formulas** (Phase 10) — `with_growth`/`with_cagr`/moving averages
  resolve to every period an input has a value, with an explicit note,
  rather than a specific (and possibly wrong) single period.
- **No live LLM was ever exercised in this sandbox** (no
  `ANTHROPIC_API_KEY`) — `AnthropicPlanner` is unit-tested against real
  `anthropic.types` objects (ground truth for the response shape) and
  live-verified request wiring (`tool_choice` forcing), but an actual
  model call has never run. Same limitation `chat.py` already documented.
- **Discovery is unverified against live APIs** for every source added in
  Phases 2-6, for the same reason: this sandbox's egress policy blocks
  every host but pypi/npm/github/anthropic. Every discovery path was
  proven against real request URLs (confirmed via live attempts that
  reached the correct endpoint and failed only on the network block) and
  offline parsing tests built from each library's own ground truth — but
  the actual response bodies were never seen. Run the `network`-marked
  tests on a machine with real internet access before trusting this in
  production.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Catalog architecture & metadata normalization | ✅ done |
| 2 | World Bank broad catalog discovery | ✅ done |
| 3 | Generalized IMF provider/catalog | ✅ done |
| 4 | Generalized Eurostat integration | ✅ done |
| 5 | OECD as first-class provider | 🚫 investigated, not safely integrable — see write-up above |
| 6 | National statistical office plugin architecture | ✅ done |
| 7 | Structured query planner + NL interface | ✅ done |
| 8 | Source/indicator selection ranking | ✅ done |
| 9 | Calculation and validation engine | ✅ done |
| 10 | Provenance/citation system | ✅ done |
| 11 | `/ask` endpoint and structured answer model | ✅ done |
| 12 | Natural-language frontend experience | ✅ done |
| 13 | Benchmarks, integration tests, hardening | ✅ done |

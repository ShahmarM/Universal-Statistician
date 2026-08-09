# Provider verification matrix (Phase A)

This document is the honest record asked for by the "real-world verification"
stage of the project: for each registered provider, how much of its
metadata-discovery → observation-retrieval path is actually grounded in real
evidence, versus documented-but-unconfirmed, versus a genuine gap.

## Why "Live verified" is NOT VERIFIED for every row

This development sandbox's network policy blocks every host except
pypi/npm/github/anthropic — confirmed directly for this phase, not assumed:

```
$ curl -sS -o /dev/null -w "%{http_code}\n" https://api.worldbank.org/v2/...
curl: (56) CONNECT tunnel failed, response 403
```

The proxy's own status endpoint records the denial explicitly:
`"kind": "connect_rejected", "detail": "gateway answered 403 to CONNECT
(policy denial or upstream failure)", "host": "api.worldbank.org:443"`.
This is a policy decision at the sandbox's network gateway, not something
code can route around. `pytest -m network` and the live smoke tests this
phase calls for genuinely could not be run from here. Per this project's own
stated principle ("never claim live verification where only mocked/offline
tests exist"), **every "Live verified" cell below is NOT VERIFIED** — that
column will only ever turn green from a run in an environment with real
outbound access to these APIs (`pytest -m network`, then update this table
with the actual results, good or bad).

What *could* be done without the network — and is the real content of this
phase — is a full **code-level audit of identifier compatibility** between
each provider's discovery path and its retrieval path: do the indicator IDs,
geography codes, and periods that discovery puts into the catalog actually
work when handed back to `get_series()`? This caught one real, previously
shipped bug (below) by static inspection alone, no live call needed.

## Bug found and fixed this phase: World Bank indicator ID format mismatch

`providers/worldbank_discovery.py::parse_indicator()` took the `id` field
straight from World Bank's v2 REST API response — which, per WB's own
documentation and this project's own test fixture, is **dot-separated**
(`"NY.GDP.PCAP.CD"`, `"SP.POP.TOTL"`). But `.` is the SDMX key *segment
separator* (`sdmx_provider.py::_build_key` joins `key_dimensions` with
`"."`), and the one verified, live-exercised worked example for this exact
source — `sdmx1`'s own integration test suite (`sdmx/tests/test_sources.py
::TestWB_WDI`) — uses the **underscore** form of the same code
(`"A.SP_POP_TOTL.AFG"`).

Concretely: if `refresh_catalog("WB_WDI")` had ever been run for real, every
discovered indicator beyond the single hand-seeded `SP_POP_TOTL` (already in
the correct form) would have gone into the catalog as `"NY.GDP.PCAP.CD"`,
and `get_series("WB_WDI", "NY.GDP.PCAP.CD", "AFG")` would have built the key
`"A.NY.GDP.PCAP.CD.AFG"` — six dot-joined segments instead of three,
malformed against WDI's `FREQ.SERIES.REF_AREA` structure. This would have
silently broken retrieval for the entire discovered catalog, not just an
edge case.

Fixed in `parse_indicator()`: the discovered `indicator_id` is now
normalized (`.` → `_`) so it's the same SDMX-retrievable spelling
`get_series()` expects; the original dot-form code (WB's public,
documented spelling) is kept in `keywords` so it's still findable by search.
Regression tests: `test_parse_indicator_normalizes_dots_to_underscores_for_
sdmx_retrieval`, updated assertions in `test_parse_indicator_builds_entry_
from_the_documented_shape` and the `network`-marked live test.

IMF, Eurostat, PX-Web, and Census were audited the same way and found
**not** to have this class of bug: their discovery and retrieval both derive
indicator codes from the exact same vocabulary (the resolved SDMX DSD's own
codelist for IMF/Eurostat; the PX-Web table's own `category.label` keys;
the Census `variables.json` keys), so there is no second, independently
formatted source of IDs to drift out of sync.

## The matrix

Status legend:
- **VERIFIED** — grounded in a dependency's own test suite that exercises
  this exact call against the live API in that dependency's CI (the
  strongest evidence tier this project uses anywhere), or (for provenance)
  directly confirmed by reading this project's own code.
- **PARTIALLY VERIFIED** — grounded in real, documented, stable API
  behavior (official docs, a payload shaped exactly like a documented
  example) but not backed by a dependency test suite, and/or has a known,
  specific gap called out in the notes.
- **NOT VERIFIED** — no ground truth beyond a plausible assumption, or the
  feature genuinely isn't implemented for this provider.

| Provider | Metadata discovery | Retrieval | Geography | Period | Unit | Provenance | Live verified |
|---|---|---|---|---|---|---|---|
| World Bank (`WB_WDI`) | PARTIALLY VERIFIED — payload shape matches WB's own documented API; ID-format bug fixed this phase | VERIFIED — sdmx1's `TestWB_WDI` exercises this exact key format live | PARTIALLY VERIFIED — `ref_area` accepted as an opaque code; discovery doesn't enumerate a country codelist | PARTIALLY VERIFIED — `startPeriod`/`endPeriod` pass through untouched; label parsing relies on generic `sdmx1` behavior, not WDI-specific confirmation | PARTIALLY VERIFIED — unit varies per indicator, not fixed at the dataflow level; `core/ask.py`'s `/ask` pipeline now falls back to the catalog's discovered per-indicator unit when the provider itself has none (Phase F), but the lower-level `compare`/`get_series` path (`tools.py::compare`) has no catalog to fall back to and still returns no unit | VERIFIED — `Attribution` always carries `source_id`/`dataset_id`/`retrieved_at`/`source_url` | NOT VERIFIED |
| IMF (`IMF_DATA_CPI`) | VERIFIED — `structure_id="DSD_CPI"` is `sdmx1`'s own verified worked example | VERIFIED — `sdmx1`'s `TestIMF_DATA` key format (`"111.CPI.CP01.IX.M"`) | VERIFIED — ref-area codelist (`CL_COUNTRY`) enumerated from the same DSD the key format is verified against | PARTIALLY VERIFIED — same generic-`sdmx1`-behavior caveat as World Bank | NOT VERIFIED — `entries_from_dsd()` doesn't extract a unit from the DSD at all; observations carry none either | VERIFIED | NOT VERIFIED |
| Eurostat (`ESTAT_NAMA_10_GDP`) | VERIFIED — `sdmx1`'s `TestESTAT.test_ss_data` walks the exact dataflow→external-reference→resolve path live | VERIFIED — same test, key format `"A.CP_MEUR.B1GQ.LU"` | VERIFIED — ref-area codelist enumerated from the same resolved DSD | PARTIALLY VERIFIED — same generic-`sdmx1`-behavior caveat | VERIFIED — unit (`"EUR million, current prices"`) and structured semantics (`price_basis="nominal"`, `currency="EUR"`) are now written onto both `IndicatorEntry.unit`/`.semantics` and every fetched `SeriesResult.unit`/`.semantics` (Phase F), grounded in Eurostat's own documented `CP_MEUR` unit code fixed by this dataflow's key | VERIFIED | NOT VERIFIED |
| Statistics Sweden / PX-Web (`SCB_TAB6471`) | PARTIALLY VERIFIED — `get_table_variables()`'s `category.label` shape is read from `pxwebpy`'s own source, not its test suite | VERIFIED — `pxwebpy`'s own test suite (`test_get_table_data_coerce_to_list`) uses this exact `value_codes` combination live | NOT VERIFIED / not applicable — `ref_area_dimension` is honestly repurposed to `Alder` (age); this table has no confirmed regional breakdown wired up | PARTIALLY VERIFIED — period format (`"2025M01"`) is grounded in `pxwebpy`'s verified test case; client-side range filtering isn't independently live-tested | NOT VERIFIED — content code `000007SF`'s real-world meaning is unconfirmed (already documented honestly in `catalog_seed.py`) | VERIFIED | NOT VERIFIED |
| US Census (`US_CENSUS_ACS1`) | PARTIALLY VERIFIED — `variables.json` shape matches Census's own documented API; no dependency test suite backs a hand-rolled REST client | PARTIALLY VERIFIED — same: documented, stable, but no dependency test suite | PARTIALLY VERIFIED — `for=state:{ref_area}` assumes FIPS state codes; discovery doesn't enumerate a geography codelist | VERIFIED — one request per plain 4-digit year is an unambiguous, explicitly-handled protocol constraint | NOT VERIFIED — `variables.json`'s `label`/`concept` fields aren't parsed for unit information at all | VERIFIED | NOT VERIFIED |
| OECD | NOT VERIFIED — not registered (see `providers/registry.py`'s module docstring and Phase I) | NOT VERIFIED — not registered | — | — | — | — | NOT VERIFIED |

## What this means for Phase B

Phase B (production catalog population) depends on discovery actually
working. Given the matrix above:

- **World Bank, IMF, Eurostat** are safe to run `ustat catalog refresh`
  against with reasonable confidence — VERIFIED or PARTIALLY VERIFIED
  discovery/retrieval, and the one real bug found is now fixed.
- **PX-Web (SCB)** discovery is plausible but less thoroughly grounded than
  the SDMX sources (discovery reads library source, not a test suite) — run
  it, but check the ingested entry count sanity before trusting it blindly.
- **Census** discovery/retrieval are documented-only, no dependency test
  suite to lean on — same caution as PX-Web.
- All of the above still need `pytest -m network` run from an environment
  with real internet access before any cell in the "Live verified" column
  can honestly turn green. This document should be updated with the actual
  results (pass/fail per check, not just "ran") the first time that happens.

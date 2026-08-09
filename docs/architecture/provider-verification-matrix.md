# Provider verification matrix (Phases A & H)

This document is the honest record asked for by the "real-world
verification" stage of the project: for each registered provider, how much
of its metadata-discovery → observation-retrieval path is actually grounded
in real evidence, versus documented-but-unconfirmed, versus a genuine gap.

**Status: live-verified.** Phase A (below, historical) could only do a
code-level audit — this sandbox's network policy blocked every official
statistics API. The user later switched this session to an environment
with unrestricted network access; `pytest -m network` and a set of direct
live investigations were then actually run against the real APIs (Phase H).
This document has been rewritten with the real results, not projected ones.

## What live verification found

### Critical, previously-shipped bug: SDMX period extraction

`SDMXProvider._to_series_result()` (shared by World Bank, IMF, and
Eurostat) built each `Observation.period` from `index_tuple[-1]` — the
*last* element of `sdmx.to_pandas()`'s resulting index tuple, assuming
`TIME_PERIOD` always sorts last. **Verified live against all three
sources, it never does — `TIME_PERIOD` is always the *first* index level:**

- World Bank: `[TIME_PERIOD, REF_AREA, SERIES, FREQ]` — `[-1]` was FREQ
  (`"A"`).
- IMF: `[TIME_PERIOD, INDEX_TYPE, COICOP_1999, TYPE_OF_TRANSFORMATION,
  COUNTRY, FREQUENCY, COMMON_REFERENCE_PERIOD, OVERLAP, SCALE,
  ACCESS_SHARING_LEVEL, SECURITY_CLASSIFICATION]` — `[-1]` was
  `SECURITY_CLASSIFICATION` (`"PUB"`).
- Eurostat: `[TIME_PERIOD, geo, na_item, unit, freq]` — `[-1]` was `freq`
  (`"A"`).

Concretely: every single observation ever retrieved through `SDMXProvider`
(World Bank, IMF, or Eurostat) had the wrong string in its `period` field —
a code like `"A"` or `"PUB"` instead of a real year/period. The offline
synthetic test fixture (`tests/conftest.py::sdmx_dataset`) declared its own
dimensions with `TIME_PERIOD` last too, so it happened to validate the same
wrong assumption the code made — both were wrong the same way, and no
offline test could have caught this without a live comparison.

**Fixed**: `_to_series_result()` now finds the `TIME_PERIOD` index level by
*name* (`series.index.names.index("TIME_PERIOD")`), not position — correct
regardless of dimension count or order. The offline fixture was corrected
to match the verified live order (`TIME_PERIOD` first), and a dedicated
regression test (`test_to_series_result_finds_time_period_by_name_not_
position`) builds a dataset with `TIME_PERIOD` in the *middle* and a
trailing dimension whose value would be wrongly grabbed by a position-based
lookup, to prove the fix is genuinely order-independent. Re-verified live
after the fix: `NY_GDP_MKTP_CD`/`AZE` now returns `2020: 42.69B, 2021:
54.83B, 2022: 78.81B, 2023: 72.43B` — real periods, plausible values (2022
spike matches the real 2022 oil/gas price surge).

### IMF: two real, live-verified corrections

1. **Discovery**: IMF's live `DSD_CPI` response does *not* put any
   dimension's codelist on `dimension.local_representation` (verified: all
   five dimensions came back `None`) — the code assumed this location,
   copying Eurostat's already-verified pattern, and had never actually been
   run live. The real, populated codelists (343 country codes, 15 COICOP
   categories, etc.) are on each dimension's *concept*'s
   `core_representation` instead, already inline in the same
   `?references=all` response — no extra request needed, just a different
   place to look. Fixed in `sdmx_discovery.py::_enumerated_codelist()`
   (checks `local_representation` first, falls back to
   `concept_identity.core_representation`).
2. **`ref_area` format**: `sdmx1`'s own bundled test fixture uses
   `ref_area="111"` (a legacy IMF numeric country code) — verified live
   that IMF's current `CL_COUNTRY` codelist no longer contains `"111"` at
   all; it only contains ISO 3166-1 alpha-3 codes (`"USA"`, `"AFG"`, ...).
   `"111.CPI.CP01.IX.M"` returns zero observations live; `"USA.CPI.CP01.
   IX.M"` returns 100+ real monthly values. This is good news for the rest
   of the system: IMF's `ref_area` convention now matches World Bank's
   (alpha-3), which is exactly what Phase G's `core/geography.py::
   resolve_geography()` already produces — no special-casing needed
   there, only the stale test fixture value was wrong.

### US Census: retrieval requires an API key (a real, corrected assumption)

Verified live: the `/data/{year}/{dataset}` query endpoint 302-redirects
(`X-DataWebAPI-KeyError: 1` header) to an HTML "missing key" page for an
unauthenticated request — not a clean 4xx, so it previously surfaced as an
opaque `JSONDecodeError`. This corrects an earlier, unverified assumption
in this project that small unauthenticated requests were accepted.
`variables.json` (discovery) does **not** require a key — verified
live too, a real, confirmed asymmetry. Fixed: `CensusProvider` now reads
`CENSUS_API_KEY` from the environment (same pattern as
`ANTHROPIC_API_KEY`) and raises a clear, actionable error
(`CensusMissingApiKeyError`) instead of a cryptic JSON error when it's
absent. No key was available in this session, so retrieval itself
remains **not independently verified end-to-end** — the mechanism is
now correct and live-tested for the failure path, but a real key is
needed to confirm the success path.

### World Bank discovery, at full live scale

`discover_wb_wdi_entries()` run for real: **1,498 indicators**, zero
retaining a dot in `indicator_id` (the Phase A fix holds at full scale, not
just in a unit test), familiar codes present with correct names
(`NY_GDP_MKTP_CD` → "GDP (current US\$)", `SP_POP_TOTL` → "Population,
total", `NY_GDP_PCAP_CD` → "GDP per capita (current US\$)",
`FP_CPI_TOTL_ZG` → "Inflation, consumer prices (annual %)"), original
dot-form codes preserved in `keywords` for search.

## The matrix

Status legend:
- **VERIFIED** — confirmed by an actual live request in this phase, or
  grounded in a dependency's own test suite that exercises this exact call
  against the live API in that dependency's CI.
- **PARTIALLY VERIFIED** — grounded in real, documented, stable API
  behavior or confirmed for part of the check, with a known, specific gap
  called out in the notes.
- **NOT VERIFIED** — no live confirmation, or the feature isn't
  implemented for this provider.

| Provider | Metadata discovery | Retrieval | Geography | Period | Unit | Provenance | Live verified |
|---|---|---|---|---|---|---|---|
| World Bank (`WB_WDI`) | **VERIFIED live** — 1,498 real indicators discovered, zero dot-form IDs remaining | **VERIFIED live** — real values retrieved for `NY_GDP_MKTP_CD`/`AZE` 2020-2023, correct periods after the TIME_PERIOD fix | PARTIALLY VERIFIED — `ref_area` accepted as an opaque alpha-3 code, live-confirmed to work; discovery doesn't separately enumerate a country codelist | **VERIFIED live** — periods confirmed correct (`2020`-`2023`, not `"A"`) after the TIME_PERIOD-by-name fix | PARTIALLY VERIFIED — unit varies per indicator, not fixed at the dataflow level; `/ask`'s catalog fallback (Phase F) covers it, the lower-level `compare`/`get_series` path does not | VERIFIED — `Attribution` always carries `source_id`/`dataset_id`/`retrieved_at`/`source_url` | **VERIFIED** |
| IMF (`IMF_DATA_CPI`) | **VERIFIED live** — real DSD fetched, concept-level codelist fallback confirmed working (COICOP categories resolved) | **VERIFIED live** — `USA`/`CP01` returns 100+ real monthly CPI values after correcting the stale `"111"` ref_area and the TIME_PERIOD fix | **VERIFIED live** — `ref_area` is alpha-3 (`CL_COUNTRY`, 343 codes, confirmed to no longer include legacy numeric codes) | **VERIFIED live** | NOT VERIFIED — `entries_from_dsd()` doesn't extract a unit from the DSD; observations carry none either | VERIFIED | **VERIFIED** |
| Eurostat (`ESTAT_NAMA_10_GDP`) | **VERIFIED live** — real DSD resolved through the external-reference follow-up | **VERIFIED live** | **VERIFIED live** — ref-area codelist enumerated from the same resolved DSD | **VERIFIED live** — confirmed correct after the TIME_PERIOD fix (`[TIME_PERIOD, geo, na_item, unit, freq]`) | VERIFIED — unit (`"EUR million, current prices"`) and structured semantics (Phase F) | VERIFIED | **VERIFIED** |
| Statistics Sweden / PX-Web (`SCB_TAB6471`) | **VERIFIED live** | **VERIFIED live** | NOT VERIFIED / not applicable — `ref_area_dimension` is honestly repurposed to `Alder` (age); no regional breakdown wired up | PARTIALLY VERIFIED — period format confirmed live; client-side range filtering not independently stress-tested | NOT VERIFIED — content code `000007SF`'s real-world meaning is still unconfirmed | VERIFIED | **VERIFIED** |
| Statistics Norway / PX-Web (`SSB_09189`) | **VERIFIED live** — 51 real macroeconomic indicators discovered and ingested | **VERIFIED live** — GDP (`bnpb.nr23_9`) confirmed for 2020-2025, real NOK-million values | NOT VERIFIED / not applicable — `ref_area_dimension` honestly repurposed to `ContentsCode` (price basis); this table has no regional dimension (Norway-wide by nature) | VERIFIED — `Tid` (year) periods confirmed correct live | NOT VERIFIED — unit not structurally captured (NOK million is documented in the table's title, not parsed) | VERIFIED | **VERIFIED** |
| US Census (`US_CENSUS_ACS1`) | **VERIFIED live** — `variables.json` fetched for real, no key required | PARTIALLY VERIFIED — the missing-key failure path is now live-confirmed and handled cleanly; the success path (with a real `CENSUS_API_KEY`) is not yet verified in this session | PARTIALLY VERIFIED — `for=state:{ref_area}` assumes FIPS state codes; discovery doesn't enumerate a geography codelist | VERIFIED — one request per plain 4-digit year is an unambiguous, explicitly-handled protocol constraint | NOT VERIFIED — `variables.json`'s `label`/`concept` fields aren't parsed for unit information | VERIFIED | PARTIALLY VERIFIED |
| OECD (`OECD_NAMAIN10`) | **VERIFIED live** — real DSD (12 dimensions) resolved directly, 308 real TRANSACTION codes discovered and ingested | PARTIALLY VERIFIED — the flagship indicator (`B1GQ`/GDP) confirmed live for 7 countries (USA, DEU, FRA, JPN, GBR, ITA, KOR), cross-checked exactly against World Bank's own figure; other TRANSACTION codes in the same 308-code discovery (e.g. `P3`, `P51G`) 404 under this dataflow's fixed non-indicator dimensions — a real, documented "advertised but not populated" gap, same category as Phase H's Eurostat/Census findings | **VERIFIED live** — `ref_area` is alpha-3, confirmed working for 7 countries | **VERIFIED live** — periods correct, `TIME_PERIOD` first in the index like every other SDMX source | VERIFIED — `unit_label`/structured semantics fixed at the dataflow level (Phase F pattern) | VERIFIED | PARTIALLY VERIFIED |

## Phase A (historical): the code-level-only audit

Before live access existed, this document recorded a code-level audit of
identifier compatibility between each provider's discovery and retrieval
paths — the one thing checkable without a network. It caught one real bug
by static inspection alone: World Bank's `worldbank_discovery.py` took
indicator codes straight from the REST API's dot-separated form
(`"NY.GDP.PCAP.CD"`), but `.` is the SDMX key segment separator, and the
verified worked example for this source uses the underscore form
(`"SP_POP_TOTL"`). Fixed in `parse_indicator()` (normalize `.` → `_`,
original form kept in `keywords`) — confirmed to hold at full live scale
above (1,498 indicators, zero remaining dots).

## What this means going forward

- **World Bank, IMF, Eurostat, PX-Web/SCB** are now genuinely,
  live-verified safe to run `ustat catalog refresh` against and to query
  through `/ask`. The critical period-extraction bug is fixed and
  live-confirmed for all three SDMX sources.
- **Census** needs a real `CENSUS_API_KEY` (free, self-serve at
  https://api.census.gov/data/key_signup.html) before its retrieval path
  can be called genuinely verified — the mechanism is correct and the
  failure mode is now honest, but the success path hasn't been observed.
- **OECD** is now registered (`OECD_NAMAIN10`, one verified dataflow —
  National Accounts, expenditure approach). The flagship GDP indicator
  (`B1GQ`) is reliable across the 7 countries spot-checked; other codes in
  the same discovered codelist are not all guaranteed to resolve under
  this dataflow's fixed key — see
  `docs/benchmarks/phase-i-oecd-report.md` for the full investigation.

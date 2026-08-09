# Phase I: revisiting OECD with current live API access

Honest record of re-investigating OECD now that this project has real,
unrestricted network access — something every earlier attempt (Phase 5,
and the live-verification round) explicitly lacked. Task instruction:
"integrate if verifiable end-to-end, otherwise leave unregistered with a
precise explanation." Result: **integrated** — one dataflow, narrowly
scoped, the same discipline already applied to Eurostat and IMF.

## Why OECD was unregistered before

`registry.py`'s module docstring has the full history. Short version:
sdmx1's own test suite had no *specific, verified* worked example for OECD
beyond one unfiltered whole-dataflow fetch, and the one genuinely verified
filtered query needed the legacy `stats.oecd.org` endpoint, which requires
downgrading the TLS handshake — the library's own maintainers document
this as disabling protection against man-in-the-middle attacks. This
project refused that trade for one narrow, discovery-incapable dataflow.

## What's different now: the current endpoint

```python
>>> sdmx.source.sources["OECD"].url
'https://sdmx.oecd.org/public/rest'
```

This is OECD's **current** official SDMX endpoint — not the deprecated
`stats.oecd.org` one the earlier investigation correctly refused to touch
insecurely. `sdmx.Client("OECD").dataflow()` lists **1,544 real
dataflows** live, no TLS workaround of any kind.

## Building a verified key: 12 dimensions, not 3

World Bank's key is 3 dimensions; Eurostat's is 4. OECD's National
Accounts DSD (`DSD_NAMAIN10`) has **12** non-time dimensions (FREQ,
REF_AREA, SECTOR, COUNTERPART_SECTOR, TRANSACTION, INSTR_ASSET, ACTIVITY,
EXPENDITURE, UNIT_MEASURE, PRICE_BASE, TRANSFORMATION, TABLE_IDENTIFIER).
Guessing plausible-looking values from each dimension's codelist and
querying directly **failed** (`400`/`404`) — several combinations that
looked reasonable from the codelist alone (e.g. `UNIT_MEASURE="USD"`,
`ACTIVITY="_T"`) simply aren't populated. The approach that worked: query
live with every non-`REF_AREA` dimension wildcarded for one country
(`A.USA...........`, curl against the raw REST endpoint) and read the
actual dimension values off a real returned row for `TRANSACTION=B1GQ`
(the GDP code):

```
A.USA.S1.S1.B1GQ._Z._Z._Z.USD_EXC.V.N.T0102 -> 26,054,614 (million USD, 2022)
```

Cross-checked against World Bank's own `NY_GDP_MKTP_CD`/`USA`/2022 figure:
**26,054,614,000,000** — exact match (26,054,614 million). Verified through
three independent paths before being pinned in `registry.py`: a raw curl
to `sdmx.oecd.org`, `sdmx1`'s own `Client.data()` call (the library this
project's `SDMXProvider` wraps), and this project's own `SDMXProvider.
get_series()`.

## Discovery: simpler than Eurostat's, matching IMF's pattern

A plain `datastructure` request for `DSD_NAMAIN10` resolves the full DSD
inline (`is_external_reference=False`) — no Eurostat-style
external-reference follow-up needed. This matches `IMFProvider`'s exact
discovery pattern, so `OECDProvider` (new,
`providers/oecd_provider.py`) reuses the same shared
`sdmx_discovery.py::entries_from_dsd()` every other discoverable SDMX
source already uses — no new architecture.

Registered as `OECD_NAMAIN10` (`providers/registry.py`), with
`{indicator}` mapped to the `TRANSACTION` dimension (SECTOR/
COUNTERPART_SECTOR/INSTR_ASSET/ACTIVITY/EXPENDITURE/UNIT_MEASURE/
PRICE_BASE/TRANSFORMATION/TABLE_IDENTIFIER all pinned to the verified
fixed values above) and `{ref_area}` to `REF_AREA`. A real
`ustat catalog refresh OECD_NAMAIN10` discovered **308 real indicators**
(TRANSACTION codes — GDP, final consumption expenditure, gross fixed
capital formation, exports, imports, compensation of employees, and other
genuine national-accounts concepts) and ingested them cleanly.

## What's verified vs. what isn't

**The flagship indicator (GDP, `B1GQ`) is reliable**: live-confirmed for 7
countries (USA, DEU, FRA, JPN, GBR, ITA, KOR), all returning real,
plausible values (Germany ~4.2T, France ~2.8T, Japan ~4.4T, UK ~3.2T,
Italy ~2.1T, South Korea ~1.8T USD for 2022).

**Not every one of the 308 discovered TRANSACTION codes is guaranteed to
resolve.** Spot-checking two other codes (`P3` — final consumption
expenditure, `P51G` — gross fixed capital formation) for countries other
than the one used to build the key returned `404 NoResultsFound` under
this dataflow's fixed non-indicator dimensions. This is the exact same
"advertised but not populated" gap Phase H already documented for
Eurostat's classification codes and US Census — a real, honestly-scoped
limitation of the discovery ≠ guaranteed-retrievable relationship that
applies across every discoverable source in this project, not something
new or specific to OECD. It means: GDP queries against `OECD_NAMAIN10`
are dependable; other national-accounts concepts from this source may
need per-code verification before being trusted, exactly as documented in
`docs/architecture/provider-verification-matrix.md`'s updated OECD row
(**PARTIALLY VERIFIED**, not fully VERIFIED).

## Tests

- `tests/test_oecd_provider.py`: 7 offline tests (key-building,
  discoverability, entry construction from a real-shaped DSD fixture,
  dimension-count mismatch handling, missing-`structure_id` handling) + 2
  `@pytest.mark.network` tests (live retrieval matching the World-Bank
  cross-check exactly; live discovery confirming the documented dimension
  layout) — both run and passed live this round.
- `tests/test_additional_sources.py`'s old
  `test_oecd_is_not_registered_pending_a_verified_discovery_example`
  replaced with `test_oecd_namain10_is_registered_with_the_live_verified_key`
  — the old test's premise (no verified example exists) is now false;
  replaced rather than deleted, so the historical reasoning it encoded
  stays checked (the generic `structure` endpoint genuinely is
  unsupported — the one part of the original claim that was correct).

**302 offline tests** (was 295 before this phase; +7 for OECD, 0 net
change elsewhere since one test was replaced, not added, in
`test_additional_sources.py`). 11 live network tests pass (1 Census test
still honestly skipped — no `CENSUS_API_KEY` in this session).

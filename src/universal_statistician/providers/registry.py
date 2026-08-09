"""Configuration for SDMX-speaking sources, consumed by SDMXProvider.

Each entry is one **queryable dataset**: a specific (SDMX source, dataflow,
fixed dimensions) combination — not necessarily "one entry per agency". This
distinction matters in practice:

- World Bank's WDI has a single dataflow whose key order (FREQ.SERIES.REF_AREA)
  is the same for every one of its ~1,500 indicators, so one entry covers the
  whole source.
- Eurostat and IMF publish many dataflows, each with its own
  DataStructureDefinition (different dimensions, different order). A single
  "ESTAT" or "IMF_DATA" entry can't express that — so each registered entry
  here is scoped to one dataflow (e.g. Eurostat's national-accounts GDP
  dataflow), with any dimension that isn't {indicator}/{ref_area} pinned to a
  fixed value. Registry ids reflect this: "ESTAT_NAMA_10_GDP", not "ESTAT".

Key formats for WB_WDI and IMF_DATA are copied from sdmx1's own integration
test suite (sdmx/tests/test_sources.py), which is exercised against the live
APIs in that project's CI — that's real ground truth, not a guess. The
Eurostat entry's dimension *order* (freq, unit, na_item, geo) is Eurostat's
documented, stable convention for national-accounts dataflows; the specific
key values are the worked example from Eurostat's own SDMX web-service docs,
also quoted in sdmx1's test suite. None of these could be exercised against
the live APIs from this environment (see the `network`-marked tests) — treat
them as verified-on-paper until an environment with network access to these
hosts confirms them.

OECD is deliberately not registered yet (re-investigated for Phase 5, see
docs/architecture/nl-platform.md for the full writeup, including a
correction of an overbroad first-pass claim caught by this project's own
regression test — test_additional_sources.py's
test_oecd_is_not_registered_because_structure_discovery_is_unsupported):

- sdmx1's bundled sources.json / `sdmx.Client("OECD").source.supports`
  marks the *generic* combined `structure` endpoint unsupported
  (`False`) for OECD — but `datastructure`, `dataflow`, `codelist`, and
  `conceptscheme` are each declared *supported* (`True`). Structure
  discovery is not blanket-impossible here the way it is for OECD_JSON
  (below); this project's first pass over-read the `False` flags and that
  was wrong — worth stating plainly rather than quietly fixing.
- What's still missing is a *specific verified worked example*: `sdmx1`'s
  own test suite (TestOECD.endpoint_args) has exactly one real,
  network-exercised OECD query — `data`, `resource_id="DSD_MSTI@DF_MSTI"`,
  with **no key** (fetches the entire dataflow, not one series) — and no
  entry at all for `datastructure`/`dataflow`/`codelist` with a specific
  resource_id. Unlike IMF (verified: `structure`, `resource_id="DSD_CPI"`)
  and Eurostat (verified: `NAMA_10_GDP`'s dataflow→structure resolution),
  there is nothing here to copy the way `entries_from_dsd()`
  (sdmx_discovery.py) needs — calling `dataflow`/`datastructure` with a
  *guessed* resource_id (even one derived from the composite
  `"DSD_MSTI@DF_MSTI"` id, whose `@`-joined format itself isn't confirmed
  to mean what it looks like it means) would be exactly the guess this
  project refuses to ship.
- The only source with a genuinely verified *filtered* query,
  `OECD_JSON` (TestOECD_JSON: `resource_id="ITF_GOODS_TRANSPORT",
  key=".T-CONT-RL-TEU+T-CONT-RL-TON"`), needs a non-generic client
  (`sdmx.source.oecd_json.Client`) because its legacy `stats.oecd.org`
  endpoint requires downgrading the SSL/TLS handshake to connect — the
  library's own maintainers document this as disabling protection against
  man-in-the-middle attacks and warn "use with caution." Not a trade this
  project makes for one narrow, discovery-incapable, legacy dataflow.

None of this rules out OECD forever: a documented worked structure-discovery
example against the current sdmx.oecd.org API (from OECD's own developer
docs, or a live environment able to inspect a dataflow's DSD directly) would
unblock it the same way Eurostat/IMF were unblocked — no new architecture
needed, only a verified SDMXSourceConfig entry and reuse of
sdmx_discovery.py::entries_from_dsd().
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SDMXSourceConfig:
    #: This entry's own key in SOURCES (e.g. "IMF_DATA_CPI"), duplicated here
    #: rather than only implicit in the dict, because it's the id every
    #: catalog entry discovered for this source must carry as
    #: IndicatorEntry.source_id — QueryEngine looks providers up by registry
    #: key, not by the underlying SDMX agency id below, and those two
    #: legitimately differ whenever more than one registry entry shares an
    #: agency (as IMF_DATA_CPI does — see `source_id`).
    registry_id: str
    #: id sdmx1 (the `sdmx` package) uses to identify the underlying source,
    #: e.g. "WB_WDI", "ESTAT". Several registry entries may share this id —
    #: this is also what ends up in a fetched SeriesResult's
    #: Attribution.source_id (see SDMXProvider._to_series_result).
    source_id: str
    source_name: str
    #: resource_id passed to Client.data() — the specific dataflow to query.
    dataflow_id: str
    #: SDMX key dimensions in this dataflow's DSD order. Each entry is either
    #: a fixed value (e.g. frequency "A", or Eurostat's unit "CP_MEUR") or a
    #: placeholder: "{indicator}" / "{ref_area}".
    key_dimensions: tuple[str, ...]
    website: str
    #: resource_id passed to Client.get("datastructure", ...) for catalog
    #: discovery (core/ingestion.py via a MetadataDiscoverable provider) —
    #: None for sources without a verified structure-discovery endpoint
    #: (see providers/worldbank_discovery.py for why World Bank uses a
    #: different mechanism entirely rather than guessing one here).
    structure_id: str | None = None
    #: How long a fetched series stays cached before being re-fetched. Official
    #: statistics are revised on the order of days/months, not minutes, so a
    #: day is a safe default for every source registered so far.
    cache_ttl_seconds: int = 86400


SOURCES: dict[str, SDMXSourceConfig] = {
    "WB_WDI": SDMXSourceConfig(
        registry_id="WB_WDI",
        source_id="WB_WDI",
        source_name="World Bank — World Development Indicators",
        dataflow_id="WDI",
        # Verified: sdmx/tests/test_sources.py::TestWB_WDI
        # (resource_id="WDI", key="A.SP_POP_TOTL.AFG")
        key_dimensions=("A", "{indicator}", "{ref_area}"),
        website="https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries",
    ),
    "IMF_DATA_CPI": SDMXSourceConfig(
        registry_id="IMF_DATA_CPI",
        source_id="IMF_DATA",
        source_name="IMF — Consumer Price Index (CPI) dataflow",
        dataflow_id="CPI",
        # Verified: sdmx/tests/test_sources.py::TestIMF_DATA
        # (resource_id="CPI", key="111.CPI.CP01.IX.M"): ref_area="111",
        # a fixed "CPI" segment, indicator=COICOP category (e.g. "CP01"),
        # then fixed index-type "IX" and monthly frequency "M".
        key_dimensions=("{ref_area}", "CPI", "{indicator}", "IX", "M"),
        website="https://data.imf.org",
        # Verified: sdmx/tests/test_sources.py::TestIMF_DATA also declares
        # "structure": dict(resource_id="DSD_CPI") and
        # "codelist": dict(resource_id="CL_COUNTRY") as real, working
        # IMF_DATA endpoints — real ground truth for structure discovery,
        # unlike World Bank (see structure_id's docstring above). See
        # providers/imf_provider.py for how this DSD is used to discover
        # every COICOP indicator this dataflow actually publishes.
        structure_id="DSD_CPI",
    ),
    "ESTAT_NAMA_10_GDP": SDMXSourceConfig(
        registry_id="ESTAT_NAMA_10_GDP",
        source_id="ESTAT",
        source_name="Eurostat — National Accounts (GDP and main aggregates)",
        dataflow_id="NAMA_10_GDP",
        # Verified: sdmx/tests/test_sources.py::TestESTAT.test_ss_data, using
        # Eurostat's documented example (unit="CP_MEUR", na_item="B1GQ",
        # geo="LU"). Scoped to current-price, million-EUR aggregates: `unit`
        # is pinned so {indicator} only needs to carry the na_item code
        # (e.g. "B1GQ" for GDP) and {ref_area} the geo code (e.g. "LU").
        key_dimensions=("A", "CP_MEUR", "{indicator}", "{ref_area}"),
        website="https://ec.europa.eu/eurostat/web/sdmx-web-services/example-queries",
    ),
}

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

OECD is deliberately not registered yet: sdmx1's own test suite queries an
OECD dataflow without a filtering key (i.e. "fetch everything"), so there is
no verified single-series example query to copy the way there is for the
other three sources. Adding OECD needs either live access to inspect a
dataflow's DSD, or a documented worked example — guessing a key order here
would risk shipping a config that fails, or worse, silently returns the
wrong series.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SDMXSourceConfig:
    #: id sdmx1 (the `sdmx` package) uses to identify the underlying source,
    #: e.g. "WB_WDI", "ESTAT". Several registry entries may share this id.
    source_id: str
    source_name: str
    #: resource_id passed to Client.data() — the specific dataflow to query.
    dataflow_id: str
    #: SDMX key dimensions in this dataflow's DSD order. Each entry is either
    #: a fixed value (e.g. frequency "A", or Eurostat's unit "CP_MEUR") or a
    #: placeholder: "{indicator}" / "{ref_area}".
    key_dimensions: tuple[str, ...]
    website: str


SOURCES: dict[str, SDMXSourceConfig] = {
    "WB_WDI": SDMXSourceConfig(
        source_id="WB_WDI",
        source_name="World Bank — World Development Indicators",
        dataflow_id="WDI",
        # Verified: sdmx/tests/test_sources.py::TestWB_WDI
        # (resource_id="WDI", key="A.SP_POP_TOTL.AFG")
        key_dimensions=("A", "{indicator}", "{ref_area}"),
        website="https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries",
    ),
    "IMF_DATA_CPI": SDMXSourceConfig(
        source_id="IMF_DATA",
        source_name="IMF — Consumer Price Index (CPI) dataflow",
        dataflow_id="CPI",
        # Verified: sdmx/tests/test_sources.py::TestIMF_DATA
        # (resource_id="CPI", key="111.CPI.CP01.IX.M"): ref_area="111",
        # a fixed "CPI" segment, indicator=COICOP category (e.g. "CP01"),
        # then fixed index-type "IX" and monthly frequency "M".
        key_dimensions=("{ref_area}", "CPI", "{indicator}", "IX", "M"),
        website="https://data.imf.org",
    ),
    "ESTAT_NAMA_10_GDP": SDMXSourceConfig(
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

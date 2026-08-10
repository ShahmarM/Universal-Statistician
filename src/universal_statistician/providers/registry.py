"""Configuration for SDMX-speaking sources, consumed by SDMXProvider.

Each entry is one **queryable dataset** — a (source, dataflow, fixed
dimensions) combination, not "one entry per agency": multi-dataflow
agencies (Eurostat, IMF, OECD) get one entry per verified dataflow, with
every non-{indicator}/{ref_area} dimension pinned. Key formats come from
verified worked examples (sdmx1's live-exercised test suite, or this
project's own live verification for OECD) — never guessed from codelists.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_statistician.core.models import StatisticalSemantics


@dataclass(frozen=True)
class SDMXSourceConfig:
    #: This entry's key in SOURCES — what catalog entries carry as
    #: source_id. Differs from `source_id` when several entries share one
    #: SDMX agency.
    registry_id: str
    #: sdmx1's id for the underlying source (e.g. "ESTAT"); also what ends
    #: up in Attribution.source_id.
    source_id: str
    source_name: str
    #: resource_id passed to Client.data().
    dataflow_id: str
    #: Key dimensions in DSD order: fixed values or "{indicator}"/
    #: "{ref_area}" placeholders.
    key_dimensions: tuple[str, ...]
    website: str
    #: resource_id for structure discovery; None when no verified endpoint.
    structure_id: str | None = None
    #: Set only when key_dimensions pin the unit for the whole dataflow —
    #: a structural fact, never a per-indicator guess.
    unit_label: str | None = None
    #: Same "only when structurally certain" rule as unit_label.
    semantics: StatisticalSemantics | None = None
    #: Official statistics revise on the order of days/months; a day is a
    #: safe default TTL.
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
        # Verified via sdmx1's TestIMF_DATA (resource_id="DSD_CPI").
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
        # CP_MEUR (pinned above) is Eurostat's documented code for "current
        # prices, million euro" — a structural fact about the dataflow.
        unit_label="EUR million, current prices",
        semantics=StatisticalSemantics(
            price_basis="nominal", currency="EUR", currency_scale="millions"
        ),
    ),
    "OECD_NAMAIN10": SDMXSourceConfig(
        registry_id="OECD_NAMAIN10",
        source_id="OECD",
        source_name="OECD — National Accounts (GDP and main aggregates, expenditure approach)",
        dataflow_id="DSD_NAMAIN10@DF_TABLE1_EXPENDITURE",
        # Verified live against sdmx.oecd.org/public/rest and cross-checked
        # against World Bank's GDP figure (2022 USA, exact match). Pinned:
        # SECTOR/COUNTERPART_SECTOR=S1 (total economy), INSTR_ASSET/
        # ACTIVITY/EXPENDITURE=_Z, UNIT_MEASURE=USD_EXC, PRICE_BASE=V
        # (current prices), TRANSFORMATION=N, TABLE_IDENTIFIER=T0102;
        # {indicator} carries the TRANSACTION code (e.g. "B1GQ").
        key_dimensions=(
            "A", "{ref_area}", "S1", "S1", "{indicator}", "_Z", "_Z", "_Z",
            "USD_EXC", "V", "N", "T0102",
        ),
        website="https://data-explorer.oecd.org/",
        structure_id="DSD_NAMAIN10",
        unit_label="US$ (exchange rate converted), current prices",
        semantics=StatisticalSemantics(price_basis="nominal", currency="USD"),
    ),
}

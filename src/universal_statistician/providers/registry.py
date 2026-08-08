"""Configuration for SDMX-speaking sources, consumed by SDMXProvider.

Adding a new SDMX source (Eurostat, IMF, OECD, ...) means adding an
SDMXSourceConfig entry here — the provider code in sdmx_provider.py does not
change. Key formats below were verified against sdmx1's own integration test
suite (sdmx/tests/test_sources.py), since each SDMX source expects its query
key's dimensions in its own DSD-defined order.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SDMXSourceConfig:
    #: id sdmx1 (the `sdmx` package) uses to identify this source, e.g. "WB_WDI".
    source_id: str
    source_name: str
    #: resource_id passed to Client.data() — the dataflow to query.
    dataflow_id: str
    #: SDMX key dimensions in this source's expected order. Each entry is either
    #: a fixed value (e.g. frequency "A") or a placeholder: "{indicator}" / "{ref_area}".
    key_dimensions: tuple[str, ...]
    website: str


SOURCES: dict[str, SDMXSourceConfig] = {
    "WB_WDI": SDMXSourceConfig(
        source_id="WB_WDI",
        source_name="World Bank — World Development Indicators",
        dataflow_id="WDI",
        key_dimensions=("A", "{indicator}", "{ref_area}"),
        website="https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries",
    ),
}

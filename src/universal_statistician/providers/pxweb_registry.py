"""Configuration for PX-Web sources, consumed by PXWebProvider.

PX-Web is the second SDMX-like standard flagged in plan.md's original
research: several national statistics offices (Sweden's SCB, Norway's SSB,
Statistics Finland) publish data through it instead of SDMX. Adding it here
— without touching Provider, QueryEngine, Catalog, Cache, compose.py,
tools.py, or any interface (MCP/CLI/API/dashboard) — is the concrete proof
that the architecture supports non-SDMX sources, which was the point of
plan.md step 9c.

This deliberately does *not* reimplement PX-Web's wire protocol (a JSON-stat2
variant with dimension/value arrays and a non-trivial query-building and
wildcard-expansion process). `pxwebpy` (PyPI: pxwebpy, import name `pxweb`)
is an actively maintained, tested Python client for it — the same
"don't write your own client" principle already applied to sdmx1 for SDMX.

Ground truth for the one source registered below (SCB table TAB6471) is
pxwebpy's own test suite (tests/test_api.py::test_get_table_data_coerce_to_list),
which uses exactly this value-code combination against the live API in that
project's CI. As with the SDMX sources, none of this could be exercised live
from this sandbox — see the `network`-marked tests.

TAB6471 has no confirmed geographic dimension in the verified test case, so
`ref_area` is deliberately repurposed here to select `Alder` (age) instead of
a region — documented explicitly rather than silently mismatched. A future
source with a genuine regional breakdown should map `ref_area_dimension` to
that instead; nothing about PXWebProvider assumes this reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PXWebSourceConfig:
    #: This entry's own key in PXWEB_SOURCES (e.g. "SCB_TAB6471") — needed
    #: for the same reason SDMXSourceConfig.registry_id is (see registry.py):
    #: IndicatorEntry.source_id must be the key QueryEngine looks providers
    #: up by, which differs from PXWebProvider.source_id (config.api_url
    #: upper-cased, e.g. "SCB" — used in Attribution.source_id instead).
    registry_id: str
    #: Passed to pxweb.PxApi(url=...): a known shorthand ("scb", "ssb") or a
    #: full PX-Web API v2 base URL for another agency running the same API.
    api_url: str
    source_name: str
    table_id: str
    #: PX-Web variable code that `indicator_id` selects a value for.
    indicator_dimension: str
    #: PX-Web variable code that `ref_area` selects a value for.
    ref_area_dimension: str
    #: PX-Web variable code for the time dimension (fetched with a wildcard,
    #: then filtered client-side by start_period/end_period — PX-Web has no
    #: native period-range selection, only explicit codes or wildcards).
    time_dimension: str
    website: str
    #: Other dimensions this table requires, pinned to a fixed value code.
    fixed_value_codes: dict[str, str] = field(default_factory=dict)
    #: Language requested for discovery (core/ingestion.py via
    #: PXWebProvider.discover_catalog_entries()) — PxApi(url, language=...)
    #: is a real, documented pxwebpy constructor parameter (see
    #: providers/pxweb_provider.py's discovery docstring), but this project
    #: hasn't independently verified every PX-Web agency actually returns
    #: English labels for it; "en" is a reasonable default a caller can
    #: override per source once that's confirmed.
    discovery_language: str = "en"
    cache_ttl_seconds: int = 86400


PXWEB_SOURCES: dict[str, PXWebSourceConfig] = {
    "SCB_TAB6471": PXWebSourceConfig(
        registry_id="SCB_TAB6471",
        api_url="scb",
        source_name="Statistics Sweden (SCB)",
        table_id="TAB6471",
        # Verified: pxwebpy's own test suite
        # (tests/test_api.py::test_get_table_data_coerce_to_list uses
        # value_codes={"Alder": ["25"], "Tid": "2025M01", "ContentsCode": "000007SF"}).
        indicator_dimension="ContentsCode",
        ref_area_dimension="Alder",  # repurposed — see module docstring
        time_dimension="Tid",
        website="https://www.scb.se/en/services/open-data-api/api-for-the-statistical-database/",
    ),
}

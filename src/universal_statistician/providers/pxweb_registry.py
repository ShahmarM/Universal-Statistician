"""Configuration for PX-Web sources, consumed by PXWebProvider.

PX-Web is the second standard several national statistics offices publish
through instead of SDMX. The wire protocol is not reimplemented here —
`pxwebpy` is the client, same principle as sdmx1 for SDMX.

Neither registered table has a genuine geographic dimension, so
`ref_area_dimension` is repurposed to select another variable (age for
SCB, price basis for SSB) — documented rather than silently mismatched. A
source with a real regional breakdown should map it to that; nothing in
PXWebProvider assumes this reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PXWebSourceConfig:
    #: This entry's key in PXWEB_SOURCES — what QueryEngine looks providers
    #: up by, and distinct from PXWebProvider.source_id (the upper-cased
    #: api_url, which goes into Attribution).
    registry_id: str
    #: Passed to pxweb.PxApi(url=...): a known shorthand ("scb", "ssb") or a
    #: full PX-Web API v2 base URL.
    api_url: str
    source_name: str
    table_id: str
    #: PX-Web variable code that `indicator_id` selects a value for.
    indicator_dimension: str
    #: PX-Web variable code that `ref_area` selects a value for.
    ref_area_dimension: str
    #: Time dimension code, fetched with a wildcard then filtered
    #: client-side — PX-Web has no native period-range selection.
    time_dimension: str
    website: str
    #: Other dimensions this table requires, pinned to a fixed value code.
    fixed_value_codes: dict[str, str] = field(default_factory=dict)
    #: Label language for discovery; not every agency is confirmed to
    #: return English, so this is overridable per source.
    discovery_language: str = "en"
    cache_ttl_seconds: int = 86400


PXWEB_SOURCES: dict[str, PXWebSourceConfig] = {
    "SCB_TAB6471": PXWebSourceConfig(
        registry_id="SCB_TAB6471",
        api_url="scb",
        source_name="Statistics Sweden (SCB)",
        table_id="TAB6471",
        # Verified against pxwebpy's own live-exercised test suite.
        indicator_dimension="ContentsCode",
        ref_area_dimension="Alder",  # repurposed — see module docstring
        time_dimension="Tid",
        website="https://www.scb.se/en/services/open-data-api/api-for-the-statistical-database/",
    ),
    "SSB_09189": PXWebSourceConfig(
        registry_id="SSB_09189",
        api_url="ssb",
        source_name="Statistics Norway (SSB)",
        table_id="09189",
        # Live-verified: "Final expenditure and gross domestic product
        # 1970-2025". Makrost carries the macroeconomic-indicator code
        # (e.g. "bnpb.nr23_9" = GDP at market values).
        indicator_dimension="Makrost",
        # Repurposed like SCB's "Alder": this table is Norway-wide, so this
        # selects price basis instead ("Priser" = current-price NOK
        # million, "Faste" = constant 2023 prices, "Volum" = volume change).
        ref_area_dimension="ContentsCode",
        time_dimension="Tid",
        website="https://www.ssb.no/en/nasjonalregnskap-og-konjunkturer/nasjonalregnskap/statistikk/nasjonalregnskap",
    ),
}

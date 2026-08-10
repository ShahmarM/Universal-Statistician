"""World Bank indicator discovery via the v2 Indicators REST API.

Separate from SDMXProvider's data retrieval because World Bank exposes two
official APIs: get_series() uses the SDMX one (verified via sdmx1's test
suite), which has no verified *structure* request for this source, so
discovery uses WB's bespoke v2 REST API instead of guessing an SDMX route.
`GET /v2/source/2/indicator` enumerates the World Development Indicators
database — the same database the WB_WDI registry entry queries for data.

Response shape (per WB's documentation): `[pagination_metadata,
[indicator_object, ...]]`, each indicator carrying `id`, `name`, `unit`,
`sourceNote`, `sourceOrganization`, and `topics`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.http_config import upstream_timeout_seconds

#: World Bank's own catalog id for the "World Development Indicators"
#: database — scopes discovery to the same database SDMXProvider's WB_WDI
#: entry queries via SDMX, not WB's entire multi-database catalog.
WDI_SOURCE_NUMBER = "2"
DISCOVERY_URL = f"https://api.worldbank.org/v2/source/{WDI_SOURCE_NUMBER}/indicator"
DEFAULT_PAGE_SIZE = 1000


def parse_indicator(raw: dict[str, Any], *, dataset_id: str = "WDI") -> IndicatorEntry:
    """Pure conversion: one WB v2 API indicator object -> IndicatorEntry.
    Separate from the HTTP call so it is testable without network."""
    raw_id = raw["id"]

    # WB's REST API publishes dot-separated codes ("NY.GDP.PCAP.CD"), but
    # "." is the SDMX key segment separator, so a dotted id would build a
    # malformed query and make every discovered indicator unretrievable.
    # Normalized here, the one place a WB code enters the catalog.
    indicator_id = raw_id.replace(".", "_")
    name = raw.get("name") or raw_id
    description = (raw.get("sourceNote") or "").strip() or None
    unit = (raw.get("unit") or "").strip() or None

    # Per-indicator data-collecting citation — more informative than
    # `source.value`, which is just the database name (already dataset_id).
    source_organization = (raw.get("sourceOrganization") or "").strip() or None

    topics = raw.get("topics") or []
    keyword_list = [t["value"] for t in topics if isinstance(t, dict) and t.get("value")]
    # Keep WB's public dot-form code searchable even though the underscore
    # form is now the retrieval identifier.
    if raw_id != indicator_id:
        keyword_list.append(raw_id)
    keywords = tuple(keyword_list) or None

    return IndicatorEntry(
        indicator_id=indicator_id,
        source_id="WB_WDI",
        names={"en": name},
        description=description,
        dataset_id=dataset_id,
        unit=unit,
        # WDI's SDMX key pins frequency to "A"; discovery covers the same
        # dataflow.
        frequency="A",
        source_organization=source_organization,
        official_url="https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries",
        keywords=keywords,
    )


def fetch_pages(
    session: requests.Session | None = None, per_page: int = DEFAULT_PAGE_SIZE
) -> Iterator[dict[str, Any]]:
    """Yield every raw indicator object across every page of WB's v2
    Indicators API for the WDI database — one HTTP GET per page."""
    http = session or requests.Session()
    page = 1
    while True:
        response = http.get(
            DISCOVERY_URL,
            params={"format": "json", "per_page": per_page, "page": page},
            timeout=upstream_timeout_seconds(),
        )
        response.raise_for_status()
        payload = response.json()
        metadata, indicators = payload[0], payload[1] or []
        yield from indicators

        total_pages = int(metadata.get("pages", page))
        if page >= total_pages:
            break
        page += 1


def discover_wb_wdi_entries(session: requests.Session | None = None) -> list[IndicatorEntry]:
    return [parse_indicator(raw) for raw in fetch_pages(session)]

"""World Bank indicator discovery via the v2 Indicators REST API.

Deliberately separate from SDMXProvider's data retrieval: World Bank exposes
*two* different official APIs. `get_series()` (providers/sdmx_provider.py)
talks to the SDMX API, verified against sdmx1's own integration test suite
(sdmx/tests/test_sources.py::TestWB_WDI, cited in providers/registry.py).
That test suite has no verified example for an SDMX *structure* request
(codelist/dataflow/conceptscheme) for this source — only for `data` — so
guessing an SDMX-structure route for discovery would repeat exactly the
mistake this project already refused to make for OECD (see registry.py's
module docstring: no verified example, don't guess).

World Bank's *other* official API — the bespoke v2 REST API at
api.worldbank.org/v2/... — is a different, independently documented
endpoint built specifically for enumerating indicators, and is the
appropriate one for discovery: `GET /v2/source/{source_id}/indicator`
returns every indicator in one of WB's ~90 catalogued databases. Source id
"2" is the World Development Indicators database (the same one SDMXProvider's
WB_WDI registry entry queries for data) — see
https://api.worldbank.org/v2/sources?format=json, which lists it.

Response shape, per World Bank's own API documentation
(https://datahelpdesk.worldbank.org/knowledgebase/articles/898581): a
two-element JSON array `[pagination_metadata, [indicator_object, ...]]`,
each indicator object carrying `id`, `name`, `unit`, `source` (an object with
`id`/`value`), `sourceNote`, `sourceOrganization`, and `topics` (a list of
`{id, value}` objects).

Honesty check, same standard applied throughout this project: this has NOT
been exercised against the live API from this sandbox (network blocked for
every host but pypi/npm/github/anthropic). Parsing (`parse_indicator`) is
unit-tested against a payload built from the documented shape above — real
documentation, not a guess — but the one thing that genuinely needs a live
call, pagination against the real endpoint, is covered only by the
`network`-marked test in test_worldbank_discovery.py. Run that on a machine
with real internet access before trusting this in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests

from universal_statistician.core.catalog import IndicatorEntry

#: World Bank's own catalog id for the "World Development Indicators"
#: database — scopes discovery to the same database SDMXProvider's WB_WDI
#: entry queries via SDMX, not WB's entire multi-database catalog.
WDI_SOURCE_NUMBER = "2"
DISCOVERY_URL = f"https://api.worldbank.org/v2/source/{WDI_SOURCE_NUMBER}/indicator"
DEFAULT_PAGE_SIZE = 1000


def parse_indicator(raw: dict[str, Any], *, dataset_id: str = "WDI") -> IndicatorEntry:
    """Pure conversion: one WB v2 API indicator object -> IndicatorEntry.

    Kept separate from the paginated HTTP call (fetch_pages below) so it's
    testable without any network access — the same separation already used
    by SDMXProvider._to_series_result / PXWebProvider._to_series_result.
    """
    raw_id = raw["id"]

    # A real, code-provable identifier-compatibility bug (section: "metadata
    # discovery and observation retrieval use compatible identifiers"),
    # caught by static inspection rather than a live call: World Bank's own
    # v2 REST API publishes indicator codes in DOT-separated form everywhere
    # in its documentation and this endpoint's own payloads (e.g.
    # "NY.GDP.PCAP.CD" — see this module's own test fixture). But "." is the
    # SDMX key *segment separator* (sdmx_provider.py::_build_key joins
    # key_dimensions with "."), and sdmx1's own verified worked example for
    # this exact source uses the UNDERSCORE form of the same code
    # ("SP_POP_TOTL", registry.py's SOURCES["WB_WDI"] docstring citing
    # sdmx/tests/test_sources.py). A discovered indicator_id containing
    # literal dots would silently split into extra key segments and build a
    # malformed SDMX query — every indicator beyond the one hand-seeded in
    # catalog_seed.py (already in underscore form) would be unretrievable.
    # Normalizing here, at the one place a WB indicator code enters the
    # catalog, keeps `IndicatorEntry.indicator_id` always round-trippable
    # into SDMXProvider.get_series() the moment discovery ever runs for real.
    indicator_id = raw_id.replace(".", "_")
    name = raw.get("name") or raw_id
    description = (raw.get("sourceNote") or "").strip() or None
    unit = (raw.get("unit") or "").strip() or None

    # `sourceOrganization` (e.g. "World Bank national accounts data, and OECD
    # National Accounts data files.") is the actual data-collecting
    # organization/citation for *this* indicator — more informative
    # provenance than `source.value`, which is just the constant database
    # name ("World Development Indicators", already captured as dataset_id).
    source_organization = (raw.get("sourceOrganization") or "").strip() or None

    topics = raw.get("topics") or []
    keyword_list = [t["value"] for t in topics if isinstance(t, dict) and t.get("value")]
    # The original dot-form code is WB's public, documented spelling (their
    # own website/docs never show the underscore form) — keep it searchable
    # even though it's no longer the retrieval identifier.
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
        # WDI's SDMX key pins frequency to "A" (see registry.py's SOURCES);
        # discovery covers the same dataflow, so it carries the same frequency.
        frequency="A",
        source_organization=source_organization,
        official_url="https://datahelpdesk.worldbank.org/knowledgebase/articles/1886701-sdmx-api-queries",
        keywords=keywords,
    )


def fetch_pages(
    session: requests.Session | None = None, per_page: int = DEFAULT_PAGE_SIZE
) -> Iterator[dict[str, Any]]:
    """Yield every raw indicator object across every page of WB's v2
    Indicators API for the WDI database. One real HTTP GET per page — this
    is the part a network-marked test must confirm against the live API."""
    http = session or requests.Session()
    page = 1
    while True:
        response = http.get(
            DISCOVERY_URL,
            params={"format": "json", "per_page": per_page, "page": page},
            timeout=30,
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

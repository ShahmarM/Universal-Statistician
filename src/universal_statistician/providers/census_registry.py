"""Configuration for the US Census Bureau's data.census.gov REST/JSON API,
consumed by CensusProvider.

A third wire-protocol family alongside SDMX (sdmx_provider.py) and PX-Web
(pxweb_provider.py) — proof for Phase 6 (national statistical office plugin
architecture) that Provider/MetadataDiscoverable don't assume any particular
protocol shape. Census's API predates and is unrelated to both SDMX and
JSON-stat: a simple `GET .../data/{year}/{dataset}?get=...&for=...` returning
a plain 2D JSON array (header row + data rows, every value a string,
including numeric ones — a well-known quirk of this API), one request per
year rather than one request for a period range.

Scoped to the American Community Survey 1-Year Estimates
(https://www.census.gov/data/developers/data-sets/acs-1year.html), whose
"detailed table" variable codes (e.g. "B01003_001E" for total population)
have been stable for over a decade — chosen over the Population Estimates
Program specifically because PEP's variable names have changed across
vintages, which this project's ground-truth-only standard won't paper over
with a guess.

Honesty check, same standard as every other source here: this API's shape
is real, extremely stable, published documentation
(https://www.census.gov/data/developers/guidance/api-user-guide.html), not
independently verified against a live call from this sandbox (network
blocked for every host but pypi/npm/github/anthropic). See
providers/census_provider.py's docstring and the `network`-marked tests in
tests/test_census_provider.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CensusSourceConfig:
    #: This entry's own key in SOURCES — see registry.py's
    #: SDMXSourceConfig.registry_id docstring for why this must be tracked
    #: explicitly rather than assumed equal to any other identifier.
    registry_id: str
    source_name: str
    #: Path segment after /data/{year}/, e.g. "acs/acs1".
    dataset_path: str
    #: Year used for discover_catalog_entries()'s variables.json request.
    #: Census publishes a variable catalog per year; table-style variable
    #: IDs are stable across years in practice, but the exact available set
    #: can shift, so discovery is pinned to one representative year rather
    #: than guessing "current" — override per source as needed.
    variables_year: str
    website: str
    cache_ttl_seconds: int = 86400


BASE_URL = "https://api.census.gov/data"

SOURCES: dict[str, CensusSourceConfig] = {
    "US_CENSUS_ACS1": CensusSourceConfig(
        registry_id="US_CENSUS_ACS1",
        source_name="US Census Bureau — American Community Survey (1-Year Estimates)",
        dataset_path="acs/acs1",
        variables_year="2022",
        website="https://www.census.gov/data/developers/data-sets/acs-1year.html",
    ),
}

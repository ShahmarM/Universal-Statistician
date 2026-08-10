"""Configuration for the US Census Bureau's REST/JSON API, consumed by
CensusProvider — a third wire-protocol family alongside SDMX and PX-Web,
showing Provider/MetadataDiscoverable assume no particular protocol shape.

Scoped to the American Community Survey 1-Year Estimates, whose detailed-
table variable codes ("B01003_001E") have been stable for over a decade;
the Population Estimates Program was rejected because its variable names
change across vintages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CensusSourceConfig:
    #: This entry's own key in SOURCES.
    registry_id: str
    source_name: str
    #: Path segment after /data/{year}/, e.g. "acs/acs1".
    dataset_path: str
    #: Census publishes a variable catalog per year and the available set
    #: can shift, so discovery is pinned to one year rather than "current".
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

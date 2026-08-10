"""Provider for the US Census Bureau's REST/JSON API — a third wire-protocol
family alongside SDMX and PX-Web, with three real differences:

1. **One request per year.** Census publishes one dataset per year and has
   no period-range parameter, so get_series() requires both start_period
   and end_period and loops over the range.
2. **A missing year/variable/geography combination is a 404**, skipped so
   one missing year doesn't fail a multi-year request; other HTTP errors
   still propagate.
3. **The data endpoint requires an API key** (`CENSUS_API_KEY`); an
   unauthenticated request 302s to an HTML page rather than returning
   JSON. Discovery's `variables.json` does not — a verified asymmetry.

Response shape: a 2D JSON array (header row, then one row per geography),
every value a string. `variables.json` is `{"variables": {CODE: {...}}}`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.census_registry import BASE_URL, CensusSourceConfig
from universal_statistician.providers.http_config import upstream_timeout_seconds

#: https://api.census.gov/data/key_signup.html
CENSUS_API_KEY_ENV_VAR = "CENSUS_API_KEY"


class CensusMissingApiKeyError(RuntimeError):
    pass

#: Geography/identity codes, not statistics — excluded from discovery.
_NON_INDICATOR_VARIABLES = {"NAME", "GEO_ID", "state", "for", "in"}


class CensusProvider(Provider):
    def __init__(self, config: CensusSourceConfig) -> None:
        self.config = config
        self.source_id = config.registry_id
        self.source_name = config.source_name
        self.cache_ttl_seconds = config.cache_ttl_seconds
        self._session: requests.Session | None = None

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def get_series(
        self,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        if not start_period or not end_period:
            raise ValueError(
                "CensusProvider.get_series() requires both start_period and end_period: "
                "the Census API publishes one dataset per year, so a time series is "
                "assembled from one request per year, not a single ranged query."
            )

        observations = []
        for year in range(int(start_period), int(end_period) + 1):
            row = self._fetch_year(str(year), indicator_id, ref_area)
            if row is not None:
                observations.append(row)

        return self._to_series_result(observations, indicator_id, ref_area)

    def _fetch_year(self, year: str, indicator_id: str, ref_area: str) -> Observation | None:
        url = f"{BASE_URL}/{year}/{self.config.dataset_path}"
        params = {"get": f"NAME,{indicator_id}", "for": f"state:{ref_area}"}
        api_key = os.environ.get(CENSUS_API_KEY_ENV_VAR)
        if api_key:
            params["key"] = api_key
        response = self._http().get(url, params=params, timeout=upstream_timeout_seconds())
        if response.status_code == 404:
            return None  # no data published for this year/variable/geography
        if response.headers.get("X-DataWebAPI-KeyError"):
            # The 302 to an HTML "missing key" page is followed by default,
            # so status is 200 and .json() would raise an opaque
            # JSONDecodeError instead of naming the real problem.
            raise CensusMissingApiKeyError(
                f"US Census API rejected the request for missing/invalid credentials "
                f"(no error from a plain 404, an HTML page instead of JSON). Set the "
                f"{CENSUS_API_KEY_ENV_VAR} environment variable — free key at "
                "https://api.census.gov/data/key_signup.html"
            )
        response.raise_for_status()
        return self._parse_year_response(response.json(), indicator_id, year)

    @staticmethod
    def _parse_year_response(rows: list[list[str]], indicator_id: str, year: str) -> Observation:
        """Pure conversion step, separated from the network call."""
        header, data_row = rows[0], rows[1]
        value_text = data_row[header.index(indicator_id)]
        value = None if value_text is None else float(value_text)
        return Observation(period=year, value=value)

    def _to_series_result(
        self, observations: list[Observation], indicator_id: str, ref_area: str
    ) -> SeriesResult:
        attribution = Attribution(
            source_id=self.source_id,
            source_name=self.source_name,
            dataset_id=self.config.dataset_path,
            retrieved_at=datetime.now(timezone.utc),
            source_url=self.config.website,
        )
        return SeriesResult(
            indicator_id=indicator_id,
            ref_area=ref_area,
            frequency="A",
            observations=tuple(sorted(observations, key=lambda o: o.period)),
            attribution=attribution,
        )

    def describe(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "dataset_path": self.config.dataset_path,
            "website": self.config.website,
        }

    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        url = f"{BASE_URL}/{self.config.variables_year}/{self.config.dataset_path}/variables.json"
        response = self._http().get(url, timeout=upstream_timeout_seconds())
        response.raise_for_status()
        return self._entries_from_variables(response.json())

    def _entries_from_variables(self, payload: dict[str, Any]) -> list[IndicatorEntry]:
        """Pure conversion step, separated from the network call."""
        entries = []
        for code, meta in payload.get("variables", {}).items():
            if code in _NON_INDICATOR_VARIABLES or not isinstance(meta, dict):
                continue
            label = meta.get("label") or code
            concept = meta.get("concept")
            entries.append(
                IndicatorEntry(
                    indicator_id=code,
                    source_id=self.config.registry_id,
                    names={"en": label},
                    description=concept,
                    dataset_id=self.config.dataset_path,
                    frequency="A",
                    source_organization=self.source_name,
                    official_url=self.config.website,
                )
            )
        return entries

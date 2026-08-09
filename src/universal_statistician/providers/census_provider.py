"""Provider for the US Census Bureau's REST/JSON API — see census_registry.py
for why this is a third, deliberately different wire-protocol family
alongside SDMX and PX-Web.

Two real protocol differences from every other provider in this project,
both handled explicitly rather than papered over:

1. **One HTTP request per year, not one request for a period range.**
   Census publishes one dataset per year (`/data/{year}/{dataset}`); there is
   no query parameter for a start/end period the way SDMX and PX-Web have.
   get_series() therefore requires *both* start_period and end_period (raises
   ValueError otherwise — this project's "no synthetic defaults" principle
   applied to a genuine protocol constraint, not a convenience shortcut) and
   loops over each year in range.
2. **A year with no data for a variable/geography combination is a 404, not
   an empty result row** — skipped explicitly (contributes no Observation
   for that period) rather than raising, so one missing year doesn't fail an
   entire multi-year request; any other HTTP error still propagates.

Response shape (see census_registry.py's docstring for the honesty caveat):
a plain 2D JSON array, `[["NAME","B01003_001E","state"], ["Alabama",
"5024279","01"]]` — header row, then one data row per requested geography
(exactly one here, since get_series always scopes `for=state:{ref_area}` to
a single area). Every value is a string, including numeric ones.
`variables.json` (discovery) is `{"variables": {"CODE": {"label": ...,
"concept": ..., "group": ..., ...}, ...}}`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.census_registry import BASE_URL, CensusSourceConfig

#: Variable codes that describe geography/identity rather than a statistic —
#: never real indicators, so discovery excludes them rather than seeding the
#: catalog with entries like "NAME: Geographic Area Name".
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
        response = self._http().get(
            url, params={"get": f"NAME,{indicator_id}", "for": f"state:{ref_area}"}, timeout=30
        )
        if response.status_code == 404:
            return None  # no data published for this year/variable/geography
        response.raise_for_status()
        return self._parse_year_response(response.json(), indicator_id, year)

    @staticmethod
    def _parse_year_response(rows: list[list[str]], indicator_id: str, year: str) -> Observation:
        """Pure conversion step, kept separate from the network call for
        offline testability — same separation as every other provider here."""
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
        response = self._http().get(url, timeout=30)
        response.raise_for_status()
        return self._entries_from_variables(response.json())

    def _entries_from_variables(self, payload: dict[str, Any]) -> list[IndicatorEntry]:
        """Pure conversion step, kept separate from the network call."""
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

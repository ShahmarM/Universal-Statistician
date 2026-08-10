"""Generic Provider backed by any PX-Web v2 source known to `pxwebpy`.

One instance per configured table, mirroring SDMXProvider's shape over a
completely different wire protocol — nothing above Provider changed to add
it.

Unlike SDMX, where each agency needed its own discovery mechanism,
PX-Web's `get_table_variables()` is already generic across agencies (it is
the same call get_series() uses for the time dimension's label, reading
one more field), so discover_catalog_entries() lives on PXWebProvider
itself rather than per-source subclasses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pxweb import PxApi

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.http_config import upstream_timeout_seconds
from universal_statistician.providers.pxweb_registry import PXWebSourceConfig


class PXWebProvider(Provider):
    def __init__(self, config: PXWebSourceConfig) -> None:
        self.config = config
        self.source_id = config.api_url.upper()
        self.source_name = config.source_name
        self.cache_ttl_seconds = config.cache_ttl_seconds
        # Lazy: PxApi.__init__ makes an eager network call (unlike
        # sdmx.Client), so building it here would make default_engine() —
        # run at startup by every interface — reach this agency's API
        # before anything asks for it, breaking startup if it's down.
        self._api_instance: PxApi | None = None
        self._table_variables: dict[str, Any] | None = None

    def _api(self) -> PxApi:
        if self._api_instance is None:
            self._api_instance = PxApi(self.config.api_url, timeout=int(upstream_timeout_seconds()))
        return self._api_instance

    def _variables(self) -> dict[str, Any]:
        """Table metadata, fetched once per instance — it is fixed for this
        config's table_id, and get_series() would otherwise spend a network
        round trip on it for every single retrieval."""
        if self._table_variables is None:
            self._table_variables = self._api().get_table_variables(self.config.table_id)
        return self._table_variables

    def get_series(
        self,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        value_codes = {
            self.config.indicator_dimension: [indicator_id],
            self.config.ref_area_dimension: [ref_area],
            self.config.time_dimension: ["*"],
            **{k: [v] for k, v in self.config.fixed_value_codes.items()},
        }
        time_label = self._variables()[self.config.time_dimension]["label"]
        rows = self._api().get_table_data(
            table_id=self.config.table_id, value_codes=value_codes, show="code"
        )
        return self._to_series_result(
            rows, time_label, indicator_id, ref_area, start_period, end_period
        )

    def _to_series_result(
        self,
        rows: list[dict[str, Any]],
        time_label: str,
        indicator_id: str,
        ref_area: str,
        start_period: str | None,
        end_period: str | None,
    ) -> SeriesResult:
        """Pure conversion step, separated from the network calls so it is
        unit-testable without network."""
        observations = []
        for row in rows:
            period = row[time_label]
            if start_period and period < start_period:
                continue
            if end_period and period > end_period:
                continue
            value = row.get("value")
            observations.append(
                Observation(period=period, value=None if value is None else float(value))
            )
        observations.sort(key=lambda o: o.period)

        attribution = Attribution(
            source_id=self.source_id,
            source_name=self.source_name,
            dataset_id=self.config.table_id,
            retrieved_at=datetime.now(timezone.utc),
            source_url=self.config.website,
        )
        return SeriesResult(
            indicator_id=indicator_id,
            ref_area=ref_area,
            frequency=self._frequency(),
            observations=tuple(observations),
            attribution=attribution,
        )

    def _frequency(self) -> str:
        # PX-Web period codes vary by table and aren't derivable generically;
        # the registered TAB6471 is monthly.
        return "M"

    def describe(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "table_id": self.config.table_id,
            "website": self.config.website,
        }

    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        # A separate PxApi rather than self._api(): this one requests a
        # specific label language, which must not change get_series()'s
        # verified language-unset behavior.
        discovery_api = PxApi(
            self.config.api_url,
            language=self.config.discovery_language,
            timeout=int(upstream_timeout_seconds()),
        )
        variables = discovery_api.get_table_variables(self.config.table_id)
        return self._entries_from_variables(variables)

    def _entries_from_variables(self, variables: dict[str, Any]) -> list[IndicatorEntry]:
        """Pure conversion step, separated from the network call."""
        indicator_var = variables.get(self.config.indicator_dimension, {})
        codes = indicator_var.get("category", {}).get("label", {})

        ref_area_var = variables.get(self.config.ref_area_dimension, {})
        ref_area_codes = tuple(sorted(ref_area_var.get("category", {}).get("label", {})))
        geographic_coverage = ref_area_codes or None

        return [
            IndicatorEntry(
                indicator_id=code,
                source_id=self.config.registry_id,
                names={self.config.discovery_language: label},
                dataset_id=self.config.table_id,
                geographic_coverage=geographic_coverage,
                source_organization=self.source_name,
                official_url=self.config.website,
            )
            for code, label in codes.items()
        ]

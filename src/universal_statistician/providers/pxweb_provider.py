"""Generic Provider backed by any PX-Web v2 source known to `pxwebpy`.

One instance per configured table (see pxweb_registry.py) — mirrors
SDMXProvider's shape exactly (get_series/describe, a pure parsing step
separated from the network call for offline testability), but talks a
completely different wire protocol underneath. That's the point: nothing
above Provider had to change to add this.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pxweb import PxApi

from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.pxweb_registry import PXWebSourceConfig


class PXWebProvider(Provider):
    def __init__(self, config: PXWebSourceConfig) -> None:
        self.config = config
        self.source_id = config.api_url.upper()
        self.source_name = config.source_name
        self.cache_ttl_seconds = config.cache_ttl_seconds
        # Deliberately NOT constructed here: PxApi(...) makes an eager network
        # call in its own __init__ (fetches /config and a table count) rather
        # than lazily connecting like sdmx.Client() does. Building it eagerly
        # would mean default_engine() — called at startup by every interface —
        # tries to reach this specific agency's API before anyone has asked
        # for anything from it, breaking app startup entirely if that host
        # is unreachable. See _api() below and its regression test.
        self._api_instance: PxApi | None = None

    def _api(self) -> PxApi:
        if self._api_instance is None:
            self._api_instance = PxApi(self.config.api_url)
        return self._api_instance

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
        variables = self._api().get_table_variables(self.config.table_id)
        time_label = variables[self.config.time_dimension]["label"]
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
        """Pure conversion step, kept separate from the network calls so it
        can be unit-tested against rows produced by pxwebpy's own
        unpack_table_data() fed a synthetic JSON-stat2 response — no network."""
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
        # PX-Web period codes vary by table; the registered TAB6471 uses
        # "YYYYMmm" (monthly). Not derivable generically without a live call,
        # so this is source-specific rather than sniffed like SDMXProvider's.
        return "M"

    def describe(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "table_id": self.config.table_id,
            "website": self.config.website,
        }

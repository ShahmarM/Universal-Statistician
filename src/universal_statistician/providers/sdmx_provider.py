"""Generic Provider backed by any SDMX source known to the `sdmx` library.

One instance per configured source (see registry.py). All the SDMX-specific
plumbing (building a key, calling the client, converting the response to
pandas) lives here exactly once; new sources are additions to the registry,
not new provider classes.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests
import sdmx

from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.http_config import upstream_timeout_seconds
from universal_statistician.providers.registry import SDMXSourceConfig

_FREQUENCIES = {"A", "Q", "M", "D"}

#: Transient upstream failures worth retrying: sustained real traffic
#: against World Bank's endpoint returns 502s under load, which are
#: capacity responses, not data problems. ConnectionError is treated alike.
_RETRYABLE_STATUS_CODES = {502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0)


class SDMXProvider(Provider):
    def __init__(self, config: SDMXSourceConfig) -> None:
        self.config = config
        self.source_id = config.source_id
        self.source_name = config.source_name
        self.cache_ttl_seconds = config.cache_ttl_seconds
        self._client = sdmx.Client(config.source_id, timeout=upstream_timeout_seconds())

    def _build_key(self, indicator_id: str, ref_area: str) -> str:
        parts = [
            dim.format(indicator=indicator_id, ref_area=ref_area)
            for dim in self.config.key_dimensions
        ]
        return ".".join(parts)

    def _frequency(self) -> str:
        fixed = [d for d in self.config.key_dimensions if d in _FREQUENCIES]
        return fixed[0] if fixed else "A"

    def get_series(
        self,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        key = self._build_key(indicator_id, ref_area)
        params = {}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period

        message = self._request_with_retry(key, params)
        dataset = message.data[0]
        return self._to_series_result(dataset, indicator_id, ref_area)

    def _request_with_retry(self, key: str, params: dict):
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._client.data(self.config.dataflow_id, key=key, params=params)
            except requests.exceptions.HTTPError as exc:
                if exc.response is None or exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    raise
                last_error = exc
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
        raise last_error

    def _to_series_result(self, dataset, indicator_id: str, ref_area: str) -> SeriesResult:
        """Pure conversion step, separated from _client.data() so it is
        unit-testable without network.

        The period is found by the TIME_PERIOD level's *name*, never a
        fixed position: to_pandas()'s MultiIndex order varies by source
        (it is first, not last, for World Bank/IMF/Eurostat), and a
        positional lookup silently returned FREQ as the "period".
        """
        series = sdmx.to_pandas(dataset)
        time_period_position = series.index.names.index("TIME_PERIOD")

        observations = tuple(
            sorted(
                (
                    Observation(
                        period=str(index_tuple[time_period_position]),
                        value=None if pd.isna(value) else float(value),
                    )
                    for index_tuple, value in series.items()
                ),
                key=lambda o: o.period,
            )
        )

        attribution = Attribution(
            source_id=self.source_id,
            source_name=self.source_name,
            dataset_id=self.config.dataflow_id,
            retrieved_at=datetime.now(timezone.utc),
            source_url=self.config.website,
        )
        return SeriesResult(
            indicator_id=indicator_id,
            ref_area=ref_area,
            frequency=self._frequency(),
            observations=observations,
            attribution=attribution,
            unit=self.config.unit_label,
            semantics=self.config.semantics,
        )

    def describe(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "dataflow_id": self.config.dataflow_id,
            "website": self.config.website,
        }

"""Shared test doubles, not a conftest fixture since these need constructor
arguments (a lookup table) that vary per test."""

from __future__ import annotations

from datetime import datetime, timezone

from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider


def make_series(indicator_id, ref_area, obs: dict[str, float | None], source_id="FAKE"):
    return SeriesResult(
        indicator_id=indicator_id,
        ref_area=ref_area,
        frequency="A",
        observations=tuple(Observation(period=p, value=v) for p, v in obs.items()),
        attribution=Attribution(
            source_id=source_id,
            source_name=f"Fake {source_id}",
            dataset_id="FAKE_DS",
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )


class LookupProvider(Provider):
    """Fake Provider returning pre-scripted series keyed by (indicator, ref_area)."""

    def __init__(self, source_id: str, table: dict[tuple[str, str], SeriesResult]) -> None:
        self.source_id = source_id
        self.source_name = f"Fake {source_id}"
        self.cache_ttl_seconds = 3600
        self._table = table

    def get_series(self, indicator_id, ref_area, *, start_period=None, end_period=None):
        return self._table[(indicator_id, ref_area)]

    def describe(self) -> dict:
        return {"source_id": self.source_id}


class FailingProvider(Provider):
    """Fake Provider simulating an upstream failure (e.g. a network-blocked
    SDMX host), to test that callers turn it into a clean error rather than
    an unhandled crash."""

    def __init__(self, source_id: str = "FAKE") -> None:
        self.source_id = source_id
        self.source_name = f"Fake {source_id}"
        self.cache_ttl_seconds = 3600

    def get_series(self, indicator_id, ref_area, *, start_period=None, end_period=None):
        raise ConnectionError("simulated upstream network failure")

    def describe(self) -> dict:
        return {"source_id": self.source_id}

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine, UnknownSourceError
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider

from .test_ingestion import DiscoverableProvider, _entry


class FakeProvider(Provider):
    """Stub Provider for exercising the engine without any real data source."""

    def __init__(self, source_id: str, cache_ttl_seconds: int = 3600) -> None:
        self.source_id = source_id
        self.source_name = f"Fake {source_id}"
        self.cache_ttl_seconds = cache_ttl_seconds
        self.calls: list[tuple[str, str]] = []

    def get_series(self, indicator_id, ref_area, *, start_period=None, end_period=None):
        self.calls.append((indicator_id, ref_area))
        return SeriesResult(
            indicator_id=indicator_id,
            ref_area=ref_area,
            frequency="A",
            observations=(Observation(period="2020", value=1.0),),
            attribution=Attribution(
                source_id=self.source_id,
                source_name=self.source_name,
                dataset_id="FAKE",
                retrieved_at=datetime.now(timezone.utc),
            ),
        )

    def describe(self) -> dict:
        return {"source_id": self.source_id, "source_name": self.source_name}


@pytest.fixture
def engine():
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="SOME_INDICATOR",
                source_id="FAKE",
                names={"en": "Some fake indicator for testing"},
            )
        ]
    )
    return QueryEngine({"FAKE": FakeProvider("FAKE")}, catalog=catalog)


def test_get_series_dispatches_to_the_right_provider(engine):
    result = engine.get_series("FAKE", "SOME_INDICATOR", "AFG")
    assert result.indicator_id == "SOME_INDICATOR"
    assert result.attribution.source_id == "FAKE"


def test_unknown_source_raises(engine):
    with pytest.raises(UnknownSourceError):
        engine.get_series("NOT_A_SOURCE", "X", "Y")


def test_list_sources_reports_every_configured_provider(engine):
    sources = engine.list_sources()
    assert sources == [{"source_id": "FAKE", "source_name": "Fake FAKE"}]


def test_search_indicator_delegates_to_catalog(engine):
    results = engine.search_indicator("fake")
    assert [r.indicator_id for r in results] == ["SOME_INDICATOR"]


def test_get_series_is_cached_on_repeat_query():
    provider = FakeProvider("FAKE")
    engine = QueryEngine({"FAKE": provider})

    first = engine.get_series("FAKE", "SOME_INDICATOR", "AFG")
    second = engine.get_series("FAKE", "SOME_INDICATOR", "AFG")

    assert len(provider.calls) == 1
    assert first == second


def test_get_series_cache_is_keyed_by_full_query():
    provider = FakeProvider("FAKE")
    engine = QueryEngine({"FAKE": provider})

    engine.get_series("FAKE", "SOME_INDICATOR", "AFG")
    engine.get_series("FAKE", "SOME_INDICATOR", "USA")
    engine.get_series("FAKE", "SOME_INDICATOR", "AFG", start_period="2020")

    assert len(provider.calls) == 3


def test_refresh_catalog_for_one_source_delegates_to_ingestion():
    catalog = Catalog()
    provider = DiscoverableProvider("FAKE", [_entry()])
    engine = QueryEngine({"FAKE": provider}, catalog=catalog)

    reports = engine.refresh_catalog("FAKE")

    assert len(reports) == 1
    assert reports[0].source_id == "FAKE"
    assert reports[0].added == 1
    assert engine.search_indicator("population")


def test_refresh_catalog_without_source_id_refreshes_every_discoverable_provider():
    catalog = Catalog()
    engine = QueryEngine(
        {
            "FAKE": DiscoverableProvider("FAKE", [_entry()]),
            "OTHER": FakeProvider("OTHER"),
        },
        catalog=catalog,
    )

    reports = engine.refresh_catalog()

    assert [r.source_id for r in reports] == ["FAKE"]

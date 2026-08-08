from __future__ import annotations

from datetime import datetime, timezone

import pytest

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine, UnknownSourceError
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.providers.base import Provider


class FakeProvider(Provider):
    """Stub Provider for exercising the engine without any real data source."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.source_name = f"Fake {source_id}"
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

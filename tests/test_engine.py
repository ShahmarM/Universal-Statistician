from __future__ import annotations

from datetime import datetime, timezone

import pytest

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import (
    QueryEngine,
    UnknownSourceError,
    _open_catalog,
    default_engine,
)
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


# ---- Performance (section 27): no full-database downloads on ordinary queries --


def test_default_engine_construction_does_not_touch_the_network():
    # Regression guard, not just implicit via other tests importing cli.py/
    # api.py/mcp_server.py: default_engine() runs at startup for every
    # interface, so it must stay network-free — SDMXProvider/PXWebProvider/
    # CensusProvider/WorldBankProvider/IMFProvider/EurostatProvider all
    # connect lazily on first use for exactly this reason (see e.g.
    # PXWebProvider's docstring for the bug this would otherwise cause).
    # No mocking needed: this sandbox's egress policy blocks every host but
    # pypi/npm/github/anthropic, so any real network attempt here would
    # raise, not silently succeed.
    engine = default_engine()
    # describe_source() raises UnknownSourceError for a key not in the
    # engine's providers dict — no exception means construction registered
    # every expected source without any of them making a network call.
    for registry_key in ("WB_WDI", "IMF_DATA_CPI", "ESTAT_NAMA_10_GDP", "SCB_TAB6471", "US_CENSUS_ACS1"):
        engine.describe_source(registry_key)


def test_get_series_never_triggers_catalog_ingestion_automatically():
    # Section 27: "Metadata ingestion can be asynchronous/offline/admin
    # -triggered" - i.e. never a side effect of an ordinary query. Proven
    # by construction: DiscoverableProvider tracks whether its discovery
    # method was called at all.
    class TrackedDiscoverableProvider(FakeProvider):
        def __init__(self, source_id: str) -> None:
            super().__init__(source_id)
            self.discovery_calls = 0

        def discover_catalog_entries(self):
            self.discovery_calls += 1
            return []

    provider = TrackedDiscoverableProvider("FAKE")
    engine = QueryEngine({"FAKE": provider})

    engine.get_series("FAKE", "SOME_INDICATOR", "AFG")
    engine.search_indicator("anything")

    assert provider.discovery_calls == 0


# ---- Catalog persistence (Phase B: "must persist between application
# restarts... do not rely on an in-memory catalog for the normal deployed
# application") --------------------------------------------------------------


def test_open_catalog_uses_in_memory_when_path_is_the_memory_sentinel(monkeypatch):
    monkeypatch.setenv("USTAT_CATALOG_DB_PATH", ":memory:")
    catalog = _open_catalog()
    # Seeded (same as every in-memory catalog default_engine() has always
    # built) — proves this path still works exactly as before Phase B.
    assert catalog.search("population")


def test_open_catalog_persists_to_a_real_file_across_separate_opens(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "catalog.db"  # parent dir doesn't exist yet
    monkeypatch.setenv("USTAT_CATALOG_DB_PATH", str(db_path))

    first = _open_catalog()
    assert db_path.exists()
    first_summary = first.summary()
    assert first_summary["indicators"] > 0  # seeded on first-ever open

    # Discover something new, simulating `ustat catalog refresh`.
    first.add([IndicatorEntry(indicator_id="X", source_id="FAKE", names={"en": "X indicator"})])

    # A fresh Catalog/connection against the same file — simulates the
    # process restarting (a new default_engine() call in a new run of the
    # CLI/API/MCP server), not just re-reading the same in-memory object.
    second = _open_catalog()
    assert second.search("X indicator")
    second_summary = second.summary()
    assert second_summary["indicators"] == first_summary["indicators"] + 1


def test_open_catalog_does_not_reseed_an_already_populated_file(tmp_path, monkeypatch):
    # Regression guard: re-seeding on every startup would silently overwrite
    # richer, discovered metadata (Phase 2-6) with catalog_seed.py's minimal
    # placeholders each time the app restarts.
    db_path = tmp_path / "catalog.db"
    monkeypatch.setenv("USTAT_CATALOG_DB_PATH", str(db_path))

    first = _open_catalog()
    first.add(
        [
            IndicatorEntry(
                indicator_id="SP_POP_TOTL",
                source_id="WB_WDI",
                names={"en": "Population, total (richer, discovered label)"},
                unit="persons",
                geographic_coverage=("AFG", "USA"),
            )
        ]
    )

    second = _open_catalog()  # "restart" against the same, now-populated file

    entry = second.get("WB_WDI", "SP_POP_TOTL")
    assert entry.unit == "persons"  # not clobbered back to the seed's bare entry
    assert entry.geographic_coverage == ("AFG", "USA")

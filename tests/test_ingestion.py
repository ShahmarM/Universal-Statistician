from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.ingestion import ingest_source, refresh_all
from universal_statistician.providers.base import Provider

from .helpers import LookupProvider


class DiscoverableProvider(Provider):
    """Fake Provider implementing MetadataDiscoverable via duck typing (the
    Protocol is structural — no base class to inherit from) — proves the
    ingestion pipeline works end to end without any real network access,
    the same "ground truth without a live call" pattern already used for
    SDMXProvider/PXWebProvider's own offline tests."""

    def __init__(self, source_id: str, entries: list[IndicatorEntry]) -> None:
        self.source_id = source_id
        self.source_name = f"Fake {source_id}"
        self.cache_ttl_seconds = 3600
        self._entries = entries

    def get_series(self, indicator_id, ref_area, *, start_period=None, end_period=None):
        raise NotImplementedError

    def describe(self) -> dict:
        return {"source_id": self.source_id}

    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        return self._entries


class FailingDiscoveryProvider(DiscoverableProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        raise ConnectionError("simulated upstream failure")


def _entry(indicator_id="POP", unit="count") -> IndicatorEntry:
    return IndicatorEntry(
        indicator_id=indicator_id,
        source_id="FAKE",
        names={"en": "Population"},
        unit=unit,
    )


def test_ingest_source_reports_error_for_non_discoverable_provider():
    catalog = Catalog()
    provider = LookupProvider("FAKE", {})

    report = ingest_source("FAKE", provider, catalog)

    assert not report.ok
    assert "does not support metadata discovery" in report.errors[0]
    assert report.added == 0
    assert catalog.get("FAKE", "POP") is None


def test_ingest_source_adds_new_entries():
    catalog = Catalog()
    provider = DiscoverableProvider("FAKE", [_entry()])

    report = ingest_source("FAKE", provider, catalog)

    assert report.ok
    assert report.added == 1
    assert report.updated == 0
    assert report.unchanged == 0
    assert catalog.get("FAKE", "POP") is not None


def test_ingest_source_is_idempotent_when_nothing_changed():
    catalog = Catalog()
    provider = DiscoverableProvider("FAKE", [_entry()])
    ingest_source("FAKE", provider, catalog)

    report = ingest_source("FAKE", provider, catalog)

    assert report.added == 0
    assert report.updated == 0
    assert report.unchanged == 1


def test_ingest_source_detects_updates():
    catalog = Catalog()
    ingest_source("FAKE", DiscoverableProvider("FAKE", [_entry(unit="count")]), catalog)

    report = ingest_source("FAKE", DiscoverableProvider("FAKE", [_entry(unit="persons")]), catalog)

    assert report.added == 0
    assert report.updated == 1
    assert report.unchanged == 0
    assert catalog.get("FAKE", "POP").unit == "persons"


def test_ingest_source_reports_discovery_failure_without_touching_catalog():
    catalog = Catalog()
    provider = FailingDiscoveryProvider("FAKE", [_entry()])

    report = ingest_source("FAKE", provider, catalog)

    assert not report.ok
    assert "Discovery failed" in report.errors[0]
    assert catalog.get("FAKE", "POP") is None


def test_refresh_all_only_touches_discoverable_providers():
    catalog = Catalog()
    providers = {
        "FAKE": DiscoverableProvider("FAKE", [_entry()]),
        "OTHER": LookupProvider("OTHER", {}),
    }

    reports = refresh_all(providers, catalog)

    assert [r.source_id for r in reports] == ["FAKE"]
    assert reports[0].added == 1

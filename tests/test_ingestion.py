from __future__ import annotations

import time

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


# ---- Performance (Phase H): a real, live-discovered issue — a single
# source's discovery can return tens of thousands of entries (US Census's
# ACS1: 36,632 variable codes), and both Catalog.get()'s FTS5 table scan
# and re-writing every entry's FTS row on every refresh (even unchanged
# ones) independently made a real `ustat catalog refresh` take minutes
# instead of well under a second. Bounded, not exact-timed (machine-
# dependent), but tight enough to catch either regression coming back. ---


def _large_provider(n: int, *, changed_name: str | None = None) -> DiscoverableProvider:
    entries = [
        IndicatorEntry(indicator_id=f"VAR_{i:06d}", source_id="FAKE", names={"en": f"Variable {i}"})
        for i in range(n)
    ]
    if changed_name is not None:
        entries[0] = IndicatorEntry(
            indicator_id=entries[0].indicator_id, source_id="FAKE", names={"en": changed_name}
        )
    return DiscoverableProvider("FAKE", entries)


def test_ingest_source_scales_to_tens_of_thousands_of_new_entries():
    catalog = Catalog()
    provider = _large_provider(20_000)

    started = time.monotonic()
    report = ingest_source("FAKE", provider, catalog)
    elapsed = time.monotonic() - started

    assert report.added == 20_000
    assert elapsed < 10  # was minutes before the Phase H fix; typically well under 1s


def test_ingest_source_repeat_refresh_of_an_unchanged_large_catalog_is_fast():
    # The realistic steady-state case: a periodic `ustat catalog refresh`
    # against a large source where nothing actually changed. Only new/
    # changed entries should touch the FTS table at all.
    catalog = Catalog()
    provider = _large_provider(20_000)
    ingest_source("FAKE", provider, catalog)

    started = time.monotonic()
    report = ingest_source("FAKE", provider, catalog)
    elapsed = time.monotonic() - started

    assert report.unchanged == 20_000
    assert report.added == 0
    assert elapsed < 5  # was minutes before the Phase H fix; typically well under 1s


def test_ingest_source_repeat_refresh_with_one_change_stays_fast():
    catalog = Catalog()
    provider = _large_provider(20_000)
    ingest_source("FAKE", provider, catalog)
    changed_provider = _large_provider(20_000, changed_name="Renamed")

    started = time.monotonic()
    report = ingest_source("FAKE", changed_provider, catalog)
    elapsed = time.monotonic() - started

    assert report.updated == 1
    assert report.unchanged == 19_999
    assert elapsed < 5
    assert catalog.get("FAKE", "VAR_000000").name == "Renamed"

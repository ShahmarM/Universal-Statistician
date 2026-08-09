"""PXWebProvider tests.

Offline tests build rows using pxwebpy's own unpack_table_data() fed a
synthetic JSON-stat2 response — the same approach used for SDMXProvider
(exercise the real library's real conversion code against a constructed
input, not a guessed shape). The live test mirrors pxwebpy's own verified
test-suite example exactly.
"""

from __future__ import annotations

import pytest
from pxweb._internal.functions import unpack_table_data

from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.pxweb_provider import PXWebProvider
from universal_statistician.providers.pxweb_registry import PXWEB_SOURCES, PXWebSourceConfig


def _json_stat2(periods: dict[str, float | None]) -> dict:
    return {
        "id": ["Alder", "ContentsCode", "Tid"],
        "dimension": {
            "Alder": {"label": "age", "category": {"label": {"25": "25 years"}}},
            "ContentsCode": {
                "label": "content",
                "category": {"label": {"000007SF": "Some measure"}},
            },
            "Tid": {"label": "month", "category": {"label": {p: p for p in periods}}},
        },
        "value": list(periods.values()),
    }


@pytest.fixture
def provider():
    return PXWebProvider(PXWEB_SOURCES["SCB_TAB6471"])


def test_construction_does_not_touch_the_network():
    # Regression test: PxApi(...) makes an eager network call in its own
    # __init__ (fetches /config and a table count), unlike sdmx.Client()
    # which connects lazily. If PXWebProvider constructed it eagerly too,
    # default_engine() — built at startup by every interface — would try to
    # reach this specific agency before anyone asked for anything from it,
    # breaking startup entirely if that host is unreachable (as it is in
    # this sandbox). Constructing with an unresolvable URL must still work;
    # only get_series() may fail.
    bogus_config = PXWebSourceConfig(
        registry_id="BOGUS",
        api_url="https://this-host-does-not-exist.invalid",
        source_name="Bogus",
        table_id="X",
        indicator_dimension="A",
        ref_area_dimension="B",
        time_dimension="Tid",
        website="https://example.org",
    )
    provider = PXWebProvider(bogus_config)  # must not raise / hit network
    assert provider._api_instance is None


def test_to_series_result_normalizes_and_filters_by_period(provider):
    rows = unpack_table_data(
        _json_stat2({"2025M01": 100.0, "2025M02": 110.0, "2025M03": 120.0}),
        show="code",
    )

    result = provider._to_series_result(
        rows, "month", "000007SF", "25", start_period="2025M02", end_period=None
    )

    assert result.indicator_id == "000007SF"
    assert result.ref_area == "25"
    assert result.frequency == "M"
    assert [o.period for o in result.observations] == ["2025M02", "2025M03"]
    assert [o.value for o in result.observations] == [110.0, 120.0]


def test_to_series_result_handles_missing_values(provider):
    rows = unpack_table_data(_json_stat2({"2025M01": None}), show="code")

    result = provider._to_series_result(rows, "month", "000007SF", "25", None, None)

    assert len(result.observations) == 1
    assert result.observations[0].period == "2025M01"
    assert result.observations[0].value is None


def test_to_series_result_attributes_to_the_configured_table(provider):
    rows = unpack_table_data(_json_stat2({"2025M01": 1.0}), show="code")

    result = provider._to_series_result(rows, "month", "000007SF", "25", None, None)

    assert result.attribution.source_id == "SCB"
    assert result.attribution.dataset_id == "TAB6471"
    assert result.attribution.source_url


def test_describe_exposes_table_id(provider):
    description = provider.describe()
    assert description["source_id"] == "SCB"
    assert description["table_id"] == "TAB6471"


def _get_table_variables_shape(*_args, **_kwargs) -> dict:
    """Reproduces PxApi.get_table_variables()'s real, documented output shape
    (pxweb/api.py: out["category"] = {"label": value["category"]["label"]}) —
    read directly from the library's own implementation, the same source of
    truth get_series() already relies on for the time dimension's label."""
    return {
        "ContentsCode": {
            "label": "content",
            "category": {"label": {"000007SF": "Some measure", "000007SG": "Another measure"}},
            "elimination": False,
            "codelists": [],
        },
        "Alder": {
            "label": "age",
            "category": {"label": {"25": "25 years", "30": "30 years"}},
            "elimination": True,
            "codelists": [],
        },
        "Tid": {"label": "month", "category": {"label": {"2025M01": "2025M01"}}, "elimination": False, "codelists": []},
    }


def test_discover_catalog_entries_builds_one_entry_per_content_code(monkeypatch, provider):
    monkeypatch.setattr(
        "universal_statistician.providers.pxweb_provider.PxApi",
        lambda *a, **kw: type("Fake", (), {"get_table_variables": staticmethod(_get_table_variables_shape)})(),
    )

    entries = provider.discover_catalog_entries()

    by_id = {e.indicator_id: e for e in entries}
    assert set(by_id) == {"000007SF", "000007SG"}
    assert by_id["000007SF"].names == {"en": "Some measure"}
    assert by_id["000007SF"].source_id == "SCB_TAB6471"  # registry key, not "SCB"
    assert by_id["000007SF"].dataset_id == "TAB6471"
    assert by_id["000007SF"].geographic_coverage == ("25", "30")


def test_pxweb_provider_is_metadata_discoverable(provider):
    assert isinstance(provider, MetadataDiscoverable)


def test_discover_catalog_entries_ingests_into_the_catalog(monkeypatch, provider):
    monkeypatch.setattr(
        "universal_statistician.providers.pxweb_provider.PxApi",
        lambda *a, **kw: type("Fake", (), {"get_table_variables": staticmethod(_get_table_variables_shape)})(),
    )

    catalog = Catalog()
    report = ingest_source("SCB_TAB6471", provider, catalog)

    assert report.ok
    assert report.added == 2
    assert catalog.get("SCB_TAB6471", "000007SF") is not None


@pytest.mark.network
def test_live_get_series_smoke():
    """Mirrors pxwebpy's own TestClient example exactly
    (tests/test_api.py::test_get_table_data_coerce_to_list). Requires network
    access this sandbox denies; run with `pytest -m network` elsewhere."""
    provider = PXWebProvider(PXWEB_SOURCES["SCB_TAB6471"])
    result = provider.get_series("000007SF", "25", start_period="2025M01")
    assert result.observations


@pytest.mark.network
def test_live_discover_catalog_entries_smoke():
    """Real call against the live SCB API — see pxweb_provider.py's
    docstring: this is the one thing the offline tests above can't confirm
    (the documented shape, not whether the live table still matches it)."""
    entries = PXWebProvider(PXWEB_SOURCES["SCB_TAB6471"]).discover_catalog_entries()
    assert entries

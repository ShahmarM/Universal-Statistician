from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers import worldbank_provider
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider
from universal_statistician.providers.worldbank_provider import WorldBankProvider


def test_worldbank_provider_is_metadata_discoverable():
    provider = WorldBankProvider(SOURCES["WB_WDI"])
    assert isinstance(provider, MetadataDiscoverable)


def test_plain_sdmx_provider_is_not_metadata_discoverable():
    # IMF/Eurostat use plain SDMXProvider and don't yet have their own
    # discovery mechanism (Phases 3-4) — the Protocol must not claim
    # otherwise just because they share a base class with WorldBankProvider.
    provider = SDMXProvider(SOURCES["IMF_DATA_CPI"])
    assert not isinstance(provider, MetadataDiscoverable)


def test_worldbank_provider_still_behaves_like_sdmx_provider(sdmx_dataset):
    # Subclassing SDMXProvider must not change get_series()/describe() —
    # same ground-truth-built dataset already used in test_sdmx_provider.py.
    provider = WorldBankProvider(SOURCES["WB_WDI"])
    dataset = sdmx_dataset({"2020": 1.5}, ref_area="AFG", indicator="SP_POP_TOTL")

    result = provider._to_series_result(dataset, "SP_POP_TOTL", "AFG")

    assert result.attribution.source_id == "WB_WDI"
    assert provider.describe()["dataflow_id"] == "WDI"


def test_worldbank_provider_discovery_ingests_into_the_catalog(monkeypatch):
    # discover_wb_wdi_entries() does a live HTTP call — replaced here with a
    # fixed offline result so this proves the WorldBankProvider ->
    # core/ingestion.py -> Catalog wiring end to end without any network.
    fake_entries = [
        IndicatorEntry(
            indicator_id="NY.GDP.PCAP.CD",
            source_id="WB_WDI",
            names={"en": "GDP per capita (current US$)"},
            dataset_id="WDI",
        )
    ]
    monkeypatch.setattr(
        worldbank_provider, "discover_wb_wdi_entries", lambda: fake_entries
    )

    catalog = Catalog()
    provider = WorldBankProvider(SOURCES["WB_WDI"])

    report = ingest_source("WB_WDI", provider, catalog)

    assert report.ok
    assert report.added == 1
    assert catalog.get("WB_WDI", "NY.GDP.PCAP.CD") is not None

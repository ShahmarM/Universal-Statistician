from __future__ import annotations

import pytest
import sdmx.message as message
from sdmx.model.common import Representation
from sdmx.model.v21 import Codelist, DataStructureDefinition, Dimension, Item, TimeDimension

from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.imf_provider import IMFDiscoveryError, IMFProvider
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider


def _build_dsd_cpi_structure_message(
    *, include_time_dimension: bool = True, include_freq_dimension: bool = True
) -> message.StructureMessage:
    """A StructureMessage shaped like a real `?references=all` response to
    `client.get("datastructure", resource_id="DSD_CPI")` — built from real
    sdmx.model.v21 classes (DataStructureDefinition/Dimension/Codelist/Item),
    the same in-memory-object approach tests/conftest.py uses for data
    messages, not a hand-guessed dict shape. See imf_provider.py's docstring
    for what's verified (the resource_id) vs not exercised live here (the
    actual response contents)."""
    country_cl = Codelist(id="CL_COUNTRY")
    country_cl.append(Item(id="111", name="Belgium"))
    country_cl.append(Item(id="134", name="Germany"))

    coicop_cl = Codelist(id="CL_CPI_COICOP")
    coicop_cl.append(Item(id="CP01", name="Food and non-alcoholic beverages"))
    coicop_cl.append(
        Item(
            id="CP02",
            name="Alcoholic beverages, tobacco and narcotics",
            description="COICOP division 02",
        )
    )

    dsd = DataStructureDefinition(id="DSD_CPI")
    ref_area = Dimension(id="REF_AREA", order=1)
    ref_area.local_representation = Representation(enumerated=country_cl)
    dsd.dimensions.append(ref_area)
    dsd.dimensions.append(Dimension(id="INDICATOR_TYPE", order=2))  # fixed "CPI" segment
    coicop = Dimension(id="COICOP", order=3)
    coicop.local_representation = Representation(enumerated=coicop_cl)
    dsd.dimensions.append(coicop)
    dsd.dimensions.append(Dimension(id="TYPE_OF_TRANSFORMATION", order=4))  # fixed "IX"
    if include_freq_dimension:
        dsd.dimensions.append(Dimension(id="FREQ", order=5))  # fixed "M"
    if include_time_dimension:
        dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=6))

    sm = message.StructureMessage()
    sm.add(country_cl)
    sm.add(coicop_cl)
    sm.add(dsd)
    return sm


@pytest.fixture
def imf_provider(monkeypatch):
    provider = IMFProvider(SOURCES["IMF_DATA_CPI"])
    monkeypatch.setattr(
        provider._client, "get", lambda *a, **kw: _build_dsd_cpi_structure_message()
    )
    return provider


def test_imf_provider_is_metadata_discoverable():
    assert isinstance(IMFProvider(SOURCES["IMF_DATA_CPI"]), MetadataDiscoverable)


def test_plain_sdmx_provider_is_not_metadata_discoverable():
    assert not isinstance(SDMXProvider(SOURCES["ESTAT_NAMA_10_GDP"]), MetadataDiscoverable)


def test_discover_catalog_entries_builds_one_entry_per_coicop_code(imf_provider):
    entries = imf_provider.discover_catalog_entries()

    by_id = {e.indicator_id: e for e in entries}
    assert set(by_id) == {"CP01", "CP02"}

    cp01 = by_id["CP01"]
    assert cp01.source_id == "IMF_DATA_CPI"  # registry key, not config.source_id ("IMF_DATA")
    assert cp01.names == {"en": "Food and non-alcoholic beverages"}
    assert cp01.dataset_id == "CPI"
    assert cp01.frequency == "M"
    assert cp01.geographic_coverage == ("111", "134")

    cp02 = by_id["CP02"]
    assert cp02.description == "COICOP division 02"


def test_discover_catalog_entries_ingests_into_the_catalog(imf_provider):
    catalog = Catalog()
    report = ingest_source("IMF_DATA_CPI", imf_provider, catalog)

    assert report.ok
    assert report.added == 2
    assert catalog.get("IMF_DATA_CPI", "CP01") is not None


def test_discover_raises_when_dimension_count_does_not_match_key_dimensions(monkeypatch):
    provider = IMFProvider(SOURCES["IMF_DATA_CPI"])
    mismatched = _build_dsd_cpi_structure_message(include_freq_dimension=False)  # 4 dims, not 5
    monkeypatch.setattr(provider._client, "get", lambda *a, **kw: mismatched)

    with pytest.raises(IMFDiscoveryError, match="4 non-time"):
        provider.discover_catalog_entries()


def test_discover_reports_cleanly_through_ingestion_on_mismatch(monkeypatch):
    provider = IMFProvider(SOURCES["IMF_DATA_CPI"])
    mismatched = _build_dsd_cpi_structure_message(include_freq_dimension=False)
    monkeypatch.setattr(provider._client, "get", lambda *a, **kw: mismatched)

    report = ingest_source("IMF_DATA_CPI", provider, Catalog())

    assert not report.ok
    assert "non-time" in report.errors[0]


def test_discover_raises_for_a_source_without_structure_id():
    provider = IMFProvider(SOURCES["ESTAT_NAMA_10_GDP"])  # no structure_id configured
    with pytest.raises(IMFDiscoveryError, match="structure_id"):
        provider.discover_catalog_entries()


@pytest.mark.network
def test_live_imf_discovery_matches_the_documented_dimension_layout():
    """Real call against the live IMF SDMX API — see imf_provider.py's
    docstring: this is the one thing the offline tests above can't confirm
    (the documented/expected shape, not whether the live DSD still
    matches it)."""
    entries = IMFProvider(SOURCES["IMF_DATA_CPI"]).discover_catalog_entries()
    assert any(e.indicator_id == "CP01" for e in entries)

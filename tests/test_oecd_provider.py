from __future__ import annotations

import pytest
import sdmx.message as message
from sdmx.model.common import Representation
from sdmx.model.v21 import Codelist, DataStructureDefinition, Dimension, Item, TimeDimension

from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.oecd_provider import OECDProvider
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_discovery import SDMXDiscoveryError
from universal_statistician.providers.sdmx_provider import SDMXProvider

#: registry.py's key_dimensions order for OECD_NAMAIN10 (see registry.py's
#: docstring for how each fixed segment was live-verified).
_NON_TIME_DIM_IDS = (
    "FREQ", "REF_AREA", "SECTOR", "COUNTERPART_SECTOR", "TRANSACTION",
    "INSTR_ASSET", "ACTIVITY", "EXPENDITURE", "UNIT_MEASURE", "PRICE_BASE",
    "TRANSFORMATION", "TABLE_IDENTIFIER",
)


def _build_dsd_namain10_structure_message(*, drop_a_dimension: bool = False) -> message.StructureMessage:
    """A StructureMessage shaped like a real `?references=all` response to
    `client.get("datastructure", resource_id="DSD_NAMAIN10")` — built from
    real sdmx.model.v21 classes, the same in-memory-object approach
    test_imf_provider.py uses. Only REF_AREA and TRANSACTION (the
    {ref_area}/{indicator} dimensions) carry a real codelist here; the
    other 10 dimensions are fixed by registry.py's key_dimensions and don't
    need one for discover_catalog_entries() to work, matching what the live
    DSD actually looks like for those (see providers/oecd_provider.py)."""
    ref_area_cl = Codelist(id="CL_REF_AREA")
    ref_area_cl.append(Item(id="USA", name="United States"))
    ref_area_cl.append(Item(id="DEU", name="Germany"))

    transaction_cl = Codelist(id="CL_TRANSACTION")
    transaction_cl.append(Item(id="B1GQ", name="Gross domestic product"))
    transaction_cl.append(
        Item(id="P3", name="Final consumption expenditure", description="ESA 2010 code P3")
    )

    dsd = DataStructureDefinition(id="DSD_NAMAIN10")
    dim_ids = list(_NON_TIME_DIM_IDS)
    if drop_a_dimension:
        dim_ids = dim_ids[:-1]  # simulate a DSD with one fewer dimension than expected
    for order, dim_id in enumerate(dim_ids, start=1):
        dim = Dimension(id=dim_id, order=order)
        if dim_id == "REF_AREA":
            dim.local_representation = Representation(enumerated=ref_area_cl)
        elif dim_id == "TRANSACTION":
            dim.local_representation = Representation(enumerated=transaction_cl)
        dsd.dimensions.append(dim)
    dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=len(dim_ids) + 1))

    sm = message.StructureMessage()
    sm.add(ref_area_cl)
    sm.add(transaction_cl)
    sm.add(dsd)
    return sm


@pytest.fixture
def oecd_provider(monkeypatch):
    provider = OECDProvider(SOURCES["OECD_NAMAIN10"])
    monkeypatch.setattr(
        provider._client, "get", lambda *a, **kw: _build_dsd_namain10_structure_message()
    )
    return provider


def test_build_key_matches_the_live_verified_key_format(oecd_provider):
    # Ground truth: live-verified directly against sdmx.oecd.org (see
    # registry.py's OECD_NAMAIN10 entry) — this exact key returned USA's
    # real GDP figures, cross-checked against World Bank's own value.
    assert (
        oecd_provider._build_key("B1GQ", "USA")
        == "A.USA.S1.S1.B1GQ._Z._Z._Z.USD_EXC.V.N.T0102"
    )


def test_oecd_provider_is_metadata_discoverable():
    assert isinstance(OECDProvider(SOURCES["OECD_NAMAIN10"]), MetadataDiscoverable)


def test_plain_sdmx_provider_is_not_metadata_discoverable():
    assert not isinstance(SDMXProvider(SOURCES["ESTAT_NAMA_10_GDP"]), MetadataDiscoverable)


def test_discover_catalog_entries_builds_one_entry_per_transaction_code(oecd_provider):
    entries = oecd_provider.discover_catalog_entries()

    by_id = {e.indicator_id: e for e in entries}
    assert set(by_id) == {"B1GQ", "P3"}

    gdp = by_id["B1GQ"]
    assert gdp.source_id == "OECD_NAMAIN10"  # registry key, not config.source_id ("OECD")
    assert gdp.names == {"en": "Gross domestic product"}
    assert gdp.dataset_id == "DSD_NAMAIN10@DF_TABLE1_EXPENDITURE"
    assert gdp.frequency == "A"
    assert set(gdp.geographic_coverage) == {"USA", "DEU"}

    p3 = by_id["P3"]
    assert p3.description == "ESA 2010 code P3"


def test_discover_catalog_entries_ingests_into_the_catalog(oecd_provider):
    catalog = Catalog()
    report = ingest_source("OECD_NAMAIN10", oecd_provider, catalog)

    assert report.ok
    assert report.added == 2
    assert catalog.get("OECD_NAMAIN10", "B1GQ") is not None


def test_discover_raises_when_dimension_count_does_not_match_key_dimensions(monkeypatch):
    provider = OECDProvider(SOURCES["OECD_NAMAIN10"])
    mismatched = _build_dsd_namain10_structure_message(drop_a_dimension=True)
    monkeypatch.setattr(provider._client, "get", lambda *a, **kw: mismatched)

    with pytest.raises(SDMXDiscoveryError, match="11 non-time"):
        provider.discover_catalog_entries()


def test_discover_raises_for_a_source_without_structure_id():
    provider = OECDProvider(SOURCES["ESTAT_NAMA_10_GDP"])  # no structure_id configured
    with pytest.raises(SDMXDiscoveryError, match="structure_id"):
        provider.discover_catalog_entries()


@pytest.mark.network
def test_live_oecd_data_smoke():
    """Real call against the live OECD SDMX API (sdmx.oecd.org/public/rest,
    the current official endpoint — no legacy stats.oecd.org TLS workaround).
    Verified live during Phase I: this exact key/country/year matches World
    Bank's own GDP figure (26,054,614 million) exactly."""
    provider = SDMXProvider(SOURCES["OECD_NAMAIN10"])
    result = provider.get_series("B1GQ", "USA", start_period="2022", end_period="2022")
    obs = {o.period: o.value for o in result.observations}
    assert obs.get("2022") == pytest.approx(26_054_614.0)


@pytest.mark.network
def test_live_oecd_discovery_matches_the_documented_dimension_layout():
    """Real call against the live OECD SDMX API — see oecd_provider.py's
    docstring: this is the one thing the offline tests above can't confirm
    (the documented/expected shape, not whether the live DSD still matches
    it)."""
    entries = OECDProvider(SOURCES["OECD_NAMAIN10"]).discover_catalog_entries()
    assert any(e.indicator_id == "B1GQ" for e in entries)

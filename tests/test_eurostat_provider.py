from __future__ import annotations

import pytest
import sdmx.message as message
from sdmx.model.common import Representation
from sdmx.model.v21 import (
    Codelist,
    DataflowDefinition,
    DataStructureDefinition,
    Dimension,
    Item,
    TimeDimension,
)

from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.eurostat_provider import EurostatProvider
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_discovery import SDMXDiscoveryError


def _resolved_nama_10_gdp_dsd() -> DataStructureDefinition:
    """Dimension order/ids consistent with key_dimensions=("A", "CP_MEUR",
    "{indicator}", "{ref_area}") -> (freq, unit, na_item, geo), matching
    sdmx1's own TestESTAT.test_ss_data example
    (key=dict(unit=["CP_MEUR"], na_item=["B1GQ"], geo=["LU"]))."""
    unit_cl = Codelist(id="CL_UNIT")
    unit_cl.append(Item(id="CP_MEUR", name="Current prices, million euro"))

    na_item_cl = Codelist(id="CL_NA_ITEM")
    na_item_cl.append(Item(id="B1GQ", name="Gross domestic product at market prices"))
    na_item_cl.append(Item(id="B1G", name="Value added, gross"))

    geo_cl = Codelist(id="CL_GEO")
    geo_cl.append(Item(id="LU", name="Luxembourg"))
    geo_cl.append(Item(id="DE", name="Germany"))

    dsd = DataStructureDefinition(id="DSD_NAMA_10_GDP")
    freq = Dimension(id="FREQ", order=1)
    dsd.dimensions.append(freq)
    unit = Dimension(id="UNIT", order=2)
    unit.local_representation = Representation(enumerated=unit_cl)
    dsd.dimensions.append(unit)
    na_item = Dimension(id="NA_ITEM", order=3)
    na_item.local_representation = Representation(enumerated=na_item_cl)
    dsd.dimensions.append(na_item)
    geo = Dimension(id="GEO", order=4)
    geo.local_representation = Representation(enumerated=geo_cl)
    dsd.dimensions.append(geo)
    dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=5))
    return dsd


class _FakeClient:
    """Reproduces sdmx1's own documented ESTAT quirk (see
    eurostat_provider.py's docstring): the first `dataflow` response's
    `.structure` is an external-reference stub, resolved only by a second
    `client.get(resource=...)` call — built from real sdmx.model.v21/message
    classes, not a hand-guessed dict shape."""

    def __init__(self, *, external_reference: bool = True):
        self.calls: list[tuple] = []
        self._external_reference = external_reference
        self._resolved_dsd = _resolved_nama_10_gdp_dsd()

    def get(self, resource_type=None, resource_id=None, **kwargs):
        self.calls.append((resource_type, resource_id, kwargs))

        if resource_type == "dataflow":
            stub = DataStructureDefinition(id="DSD_NAMA_10_GDP")
            stub.is_external_reference = self._external_reference
            if not self._external_reference:
                stub = self._resolved_dsd  # resolves in one step for this case
            dataflow = DataflowDefinition(id=resource_id)
            dataflow.structure = stub
            sm = message.StructureMessage()
            sm.add(dataflow)
            return sm

        if "resource" in kwargs:
            sm = message.StructureMessage()
            sm.add(self._resolved_dsd)
            return sm

        raise AssertionError(f"unexpected call: {resource_type=} {resource_id=} {kwargs=}")


@pytest.fixture
def eurostat_provider(monkeypatch):
    provider = EurostatProvider(SOURCES["ESTAT_NAMA_10_GDP"])
    monkeypatch.setattr(provider, "_client", _FakeClient())
    return provider


def test_eurostat_provider_is_metadata_discoverable():
    assert isinstance(EurostatProvider(SOURCES["ESTAT_NAMA_10_GDP"]), MetadataDiscoverable)


def test_discover_follows_the_external_reference_to_resolve_the_dsd(eurostat_provider):
    entries = eurostat_provider.discover_catalog_entries()

    by_id = {e.indicator_id: e for e in entries}
    assert set(by_id) == {"B1GQ", "B1G"}

    b1gq = by_id["B1GQ"]
    assert b1gq.source_id == "ESTAT_NAMA_10_GDP"
    assert b1gq.names == {"en": "Gross domestic product at market prices"}
    assert b1gq.dataset_id == "NAMA_10_GDP"
    assert b1gq.frequency == "A"
    assert b1gq.geographic_coverage == ("DE", "LU")
    # Phase F: structurally known from the registry entry (unit pinned to
    # CP_MEUR), not inferred from the indicator's name.
    assert b1gq.unit == "EUR million, current prices"
    assert b1gq.semantics.price_basis == "nominal"
    assert b1gq.semantics.currency == "EUR"

    # Two real requests: the dataflow stub, then the follow-up resolve.
    assert [c[0] for c in eurostat_provider._client.calls] == ["dataflow", None]


def test_discover_skips_the_second_request_when_dsd_is_already_resolved(monkeypatch):
    provider = EurostatProvider(SOURCES["ESTAT_NAMA_10_GDP"])
    client = _FakeClient(external_reference=False)
    monkeypatch.setattr(provider, "_client", client)

    entries = provider.discover_catalog_entries()

    assert {e.indicator_id for e in entries} == {"B1GQ", "B1G"}
    assert len(client.calls) == 1  # no follow-up "resource=" request needed


def test_discover_catalog_entries_ingests_into_the_catalog(eurostat_provider):
    catalog = Catalog()
    report = ingest_source("ESTAT_NAMA_10_GDP", eurostat_provider, catalog)

    assert report.ok
    assert report.added == 2
    assert catalog.get("ESTAT_NAMA_10_GDP", "B1GQ") is not None


@pytest.mark.network
def test_live_eurostat_discovery_matches_the_documented_dimension_layout():
    """Real call against the live Eurostat SDMX API — see this module's
    docstring: this is the one thing the offline tests above can't confirm."""
    entries = EurostatProvider(SOURCES["ESTAT_NAMA_10_GDP"]).discover_catalog_entries()
    assert any(e.indicator_id == "B1GQ" for e in entries)

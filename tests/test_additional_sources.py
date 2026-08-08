"""Coverage for the IMF_DATA_CPI and ESTAT_NAMA_10_GDP registry entries.

WB_WDI's own tests live in test_sdmx_provider.py; these mirror the same
approach (verify _build_key against sdmx1's documented ground truth, verify
_to_series_result parsing against an in-memory dataset, and provide a
skipped-by-default live smoke test) for the two sources added afterward.
"""

from __future__ import annotations

import pytest

from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider


def test_build_key_matches_imf_data_cpi_example():
    # Verified: sdmx/tests/test_sources.py::TestIMF_DATA
    # (resource_id="CPI", key="111.CPI.CP01.IX.M")
    provider = SDMXProvider(SOURCES["IMF_DATA_CPI"])
    assert provider._build_key("CP01", "111") == "111.CPI.CP01.IX.M"


def test_imf_data_cpi_frequency_is_monthly():
    provider = SDMXProvider(SOURCES["IMF_DATA_CPI"])
    assert provider._frequency() == "M"


def test_imf_data_cpi_parses_and_attributes(sdmx_dataset):
    provider = SDMXProvider(SOURCES["IMF_DATA_CPI"])
    dataset = sdmx_dataset({"2018-01": 100.0, "2018-02": 101.2}, ref_area="111", indicator="CP01")

    result = provider._to_series_result(dataset, "CP01", "111")

    assert result.frequency == "M"
    assert [o.period for o in result.observations] == ["2018-01", "2018-02"]
    assert result.attribution.source_id == "IMF_DATA"
    assert result.attribution.dataset_id == "CPI"


def test_build_key_matches_eurostat_nama_10_gdp_example():
    # Verified: sdmx/tests/test_sources.py::TestESTAT.test_ss_data, using
    # Eurostat's documented example (unit="CP_MEUR", na_item="B1GQ", geo="LU").
    provider = SDMXProvider(SOURCES["ESTAT_NAMA_10_GDP"])
    assert provider._build_key("B1GQ", "LU") == "A.CP_MEUR.B1GQ.LU"


def test_eurostat_gdp_parses_and_attributes(sdmx_dataset):
    provider = SDMXProvider(SOURCES["ESTAT_NAMA_10_GDP"])
    dataset = sdmx_dataset({"2012": 42000.0, "2013": 43500.0}, ref_area="LU", indicator="B1GQ")

    result = provider._to_series_result(dataset, "B1GQ", "LU")

    assert result.frequency == "A"
    assert [o.value for o in result.observations] == [42000.0, 43500.0]
    assert result.attribution.source_id == "ESTAT"
    assert result.attribution.dataset_id == "NAMA_10_GDP"


@pytest.mark.network
def test_live_imf_data_cpi_smoke():
    """Mirrors sdmx1's own TestIMF_DATA example exactly. Requires network
    access this sandbox denies; run with `pytest -m network` elsewhere."""
    result = SDMXProvider(SOURCES["IMF_DATA_CPI"]).get_series(
        "CP01", "111", start_period="2018"
    )
    assert result.observations


@pytest.mark.network
def test_live_eurostat_gdp_smoke():
    """Mirrors sdmx1's own TestESTAT.test_ss_data example exactly. Requires
    network access this sandbox denies; run with `pytest -m network` elsewhere."""
    result = SDMXProvider(SOURCES["ESTAT_NAMA_10_GDP"]).get_series(
        "B1GQ", "LU", start_period="2012", end_period="2015"
    )
    assert result.observations

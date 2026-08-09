"""Coverage for the IMF_DATA_CPI and ESTAT_NAMA_10_GDP registry entries.

WB_WDI's own tests live in test_sdmx_provider.py; these mirror the same
approach (verify _build_key against sdmx1's documented ground truth, verify
_to_series_result parsing against an in-memory dataset, and provide a
skipped-by-default live smoke test) for the two sources added afterward.
"""

from __future__ import annotations

import pytest
import sdmx

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
    """sdmx1's own TestIMF_DATA example uses ref_area="111" (a legacy IMF
    numeric country code) - verified live (Phase H) to no longer return any
    data: IMF's live CL_COUNTRY codelist for this dataflow only contains
    ISO 3166-1 alpha-3 codes today ("USA", "AFG", ...), not "111". "USA"
    confirmed live to return 100+ real monthly CPI observations; see
    docs/architecture/provider-verification-matrix.md."""
    result = SDMXProvider(SOURCES["IMF_DATA_CPI"]).get_series(
        "CP01", "USA", start_period="2018"
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


def test_oecd_namain10_is_registered_with_the_live_verified_key():
    """Regression guard for Phase I: OECD was deliberately left unregistered
    through Phase 5 and the live-verification round (no verified working
    example existed) — see registry.py's module docstring for that history.
    Revisited with real network access: sdmx.oecd.org/public/rest (the
    CURRENT official endpoint, not the deprecated stats.oecd.org one that
    needed an unsafe legacy TLS downgrade) does support a genuine, specific,
    verified `datastructure` query, unlike what sdmx1's own test suite alone
    could confirm. One dataflow (National Accounts, expenditure approach)
    is now registered as "OECD_NAMAIN10" — see providers/oecd_provider.py
    and registry.py for the full investigation."""
    assert "OECD_NAMAIN10" in SOURCES

    config = SOURCES["OECD_NAMAIN10"]
    assert config.source_id == "OECD"
    assert config.structure_id == "DSD_NAMAIN10"

    source = sdmx.Client("OECD").source
    assert source.supports[sdmx.Resource.datastructure] is True
    # The generic combined "structure" endpoint (distinct from the more
    # specific "datastructure" endpoint entries_from_dsd() actually uses)
    # genuinely is unsupported — the one accurate part of the original,
    # pre-Phase-I claim that OECD couldn't be safely registered.
    assert source.supports[sdmx.Resource.structure] is False

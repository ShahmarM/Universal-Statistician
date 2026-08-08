from __future__ import annotations

import pytest

from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider


@pytest.fixture
def wb_provider():
    return SDMXProvider(SOURCES["WB_WDI"])


def test_build_key_matches_wb_wdi_dimension_order(wb_provider):
    # Ground truth for WB_WDI's key order (FREQ.SERIES.REF_AREA) comes from
    # sdmx1's own integration test suite (sdmx/tests/test_sources.py::TestWB_WDI).
    assert wb_provider._build_key("SP_POP_TOTL", "AFG") == "A.SP_POP_TOTL.AFG"


def test_to_series_result_normalizes_and_attributes(wb_provider, sdmx_dataset):
    dataset = sdmx_dataset(
        {"2019": 100.0, "2018": 90.0, "2020": float("nan")},
        ref_area="AFG",
        indicator="SP_POP_TOTL",
    )

    result = wb_provider._to_series_result(dataset, "SP_POP_TOTL", "AFG")

    assert result.indicator_id == "SP_POP_TOTL"
    assert result.ref_area == "AFG"
    assert result.frequency == "A"

    # chronologically sorted, regardless of insertion order
    assert [o.period for o in result.observations] == ["2018", "2019", "2020"]
    assert [o.value for o in result.observations] == [90.0, 100.0, None]

    attribution = result.attribution
    assert attribution.source_id == "WB_WDI"
    assert attribution.dataset_id == "WDI"
    assert attribution.source_url
    assert attribution.retrieved_at is not None


def test_as_dict_is_json_friendly(wb_provider, sdmx_dataset):
    dataset = sdmx_dataset({"2020": 1.5}, ref_area="AFG", indicator="SP_POP_TOTL")
    result = wb_provider._to_series_result(dataset, "SP_POP_TOTL", "AFG")

    payload = result.as_dict()
    assert payload["observations"] == [{"period": "2020", "value": 1.5}]
    assert payload["attribution"]["source_id"] == "WB_WDI"
    assert isinstance(payload["attribution"]["retrieved_at"], str)


def test_describe_exposes_attribution_fields(wb_provider):
    description = wb_provider.describe()
    assert description["source_id"] == "WB_WDI"
    assert description["dataflow_id"] == "WDI"
    assert description["website"].startswith("https://")


@pytest.mark.network
def test_live_get_series_smoke():
    """Real call against the World Bank SDMX API — requires outbound network
    access that this sandbox's egress policy denies (api.worldbank.org: 403).
    Run explicitly with `pytest -m network` on a machine that allows it."""
    result = SDMXProvider(SOURCES["WB_WDI"]).get_series(
        "SP_POP_TOTL", "AFG", start_period="2011", end_period="2011"
    )
    assert result.observations
    assert result.observations[0].value is not None

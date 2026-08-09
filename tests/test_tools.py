from __future__ import annotations

import pytest

from universal_statistician import tools
from universal_statistician.core.engine import QueryEngine

from .helpers import LookupProvider, make_series


@pytest.fixture
def engine():
    provider = LookupProvider(
        "FAKE",
        {
            ("POP", "AFG"): make_series("POP", "AFG", {"2019": 10.0, "2020": 11.0}),
            ("POP", "USA"): make_series("POP", "USA", {"2019": 300.0, "2020": 310.0}),
            ("GDP", "AFG"): make_series("GDP", "AFG", {"2020": 20.0}),
        },
    )
    return QueryEngine({"FAKE": provider})


def test_get_series_returns_json_friendly_dict(engine):
    payload = tools.get_series(engine, "FAKE", "POP", "AFG")
    assert payload["indicator_id"] == "POP"
    assert payload["observations"] == [
        {"period": "2019", "value": 10.0, "status": None},
        {"period": "2020", "value": 11.0, "status": None},
    ]
    assert payload["attribution"]["source_id"] == "FAKE"


def test_list_sources_delegates_to_engine(engine):
    assert tools.list_sources(engine) == [{"source_id": "FAKE"}]


def test_describe_source_delegates_to_engine(engine):
    assert tools.describe_source(engine, "FAKE") == {"source_id": "FAKE"}


def test_search_indicator_returns_dicts():
    from universal_statistician.core.catalog import Catalog, IndicatorEntry

    catalog = Catalog()
    catalog.add([IndicatorEntry(indicator_id="POP", source_id="FAKE", names={"en": "Population"})])
    provider = LookupProvider("FAKE", {})
    engine = QueryEngine({"FAKE": provider}, catalog=catalog)

    results = tools.search_indicator(engine, "population")
    assert len(results) == 1
    assert results[0]["indicator_id"] == "POP"
    assert results[0]["name"] == "Population"
    assert results[0]["source_id"] == "FAKE"
    assert results[0]["description"] is None
    # Newer optional metadata fields (dataset_id, unit, dimensions, ...) are
    # present but unset for an entry seeded without them — see IndicatorMeta.
    assert results[0]["dataset_id"] is None
    assert results[0]["dimensions"] is None


def test_compare_cross_country_mode(engine):
    payload = tools.compare(engine, "FAKE", indicator_id="POP", ref_areas=["AFG", "USA"])
    assert [c["key"] for c in payload["columns"]] == ["AFG", "USA"]
    row_2020 = next(r for r in payload["rows"] if r["period"] == "2020")
    assert row_2020 == {"period": "2020", "AFG": 11.0, "USA": 310.0}


def test_compare_cross_indicator_mode(engine):
    payload = tools.compare(engine, "FAKE", indicator_ids=["POP", "GDP"], ref_area="AFG")
    assert [c["key"] for c in payload["columns"]] == ["POP", "GDP"]


def test_compare_applies_growth_ratio_and_rank(engine):
    payload = tools.compare(
        engine,
        "FAKE",
        indicator_id="POP",
        ref_areas=["AFG", "USA"],
        growth=True,
        rank=True,
        ratio_to="USA",
    )
    keys = {c["key"] for c in payload["columns"]}
    assert "AFG__yoy_growth_pct" in keys
    assert "USA__yoy_growth_pct" in keys
    assert "AFG__rank" in keys
    assert "AFG__ratio_to_USA" in keys
    assert "USA__ratio_to_USA" not in keys


def test_compare_requires_a_valid_query_shape(engine):
    with pytest.raises(ValueError):
        tools.compare(engine, "FAKE")

    with pytest.raises(ValueError):
        tools.compare(engine, "FAKE", indicator_id="POP")  # missing ref_areas

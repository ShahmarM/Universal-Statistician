from __future__ import annotations

import pytest

from universal_statistician.core.compose import (
    ComparisonColumn,
    build_comparison,
    compare_across_countries,
    compare_across_indicators,
    with_growth,
    with_rank,
    with_ratio,
)
from universal_statistician.core.engine import QueryEngine

from .helpers import LookupProvider, make_series as _series


# ---- build_comparison / compare_across_* --------------------------------


def test_build_comparison_aligns_on_period_axis():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2019": 10.0, "2020": 11.0})),
            (col_b, _series("POP", "USA", {"2020": 300.0})),
        ]
    )

    assert table.periods() == ("2019", "2020")
    assert table.value_at("2019", "AFG") == 10.0
    assert table.value_at("2019", "USA") is None
    assert table.value_at("2020", "USA") == 300.0


def test_compare_across_countries_fetches_one_series_per_country():
    provider = LookupProvider(
        "FAKE",
        {
            ("POP", "AFG"): _series("POP", "AFG", {"2020": 10.0}),
            ("POP", "USA"): _series("POP", "USA", {"2020": 300.0}),
        },
    )
    engine = QueryEngine({"FAKE": provider})

    table = compare_across_countries(engine, "FAKE", "POP", ["AFG", "USA"])

    assert [c.key for c in table.columns] == ["AFG", "USA"]
    assert table.value_at("2020", "AFG") == 10.0
    assert table.value_at("2020", "USA") == 300.0
    assert table.columns[0].attribution.source_id == "FAKE"


def test_compare_across_indicators_fetches_one_series_per_indicator():
    provider = LookupProvider(
        "FAKE",
        {
            ("GDP", "LU"): _series("GDP", "LU", {"2020": 50.0}),
            ("UNEMP", "LU"): _series("UNEMP", "LU", {"2020": 5.0}),
        },
    )
    engine = QueryEngine({"FAKE": provider})

    table = compare_across_indicators(engine, "FAKE", ["GDP", "UNEMP"], "LU")

    assert [c.key for c in table.columns] == ["GDP", "UNEMP"]
    assert table.value_at("2020", "GDP") == 50.0
    assert table.value_at("2020", "UNEMP") == 5.0


# ---- with_growth ----------------------------------------------------------


def test_with_growth_computes_percent_change():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2019": 100.0, "2020": 110.0}))])

    result = with_growth(table)

    growth_col = next(c for c in result.columns if c.key == "AFG__yoy_growth_pct")
    assert growth_col.derived is True
    assert result.value_at("2019", "AFG__yoy_growth_pct") is None  # no prior period
    assert result.value_at("2020", "AFG__yoy_growth_pct") == pytest.approx(10.0)


def test_with_growth_skips_zero_baseline():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2019": 0.0, "2020": 110.0}))])

    result = with_growth(table)

    assert result.value_at("2020", "AFG__yoy_growth_pct") is None


def test_with_growth_does_not_recompute_existing_derived_columns():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2019": 100.0, "2020": 110.0}))])

    once = with_growth(table)
    twice = with_growth(once)

    derived_keys = [c.key for c in twice.columns if c.derived]
    assert derived_keys.count("AFG__yoy_growth_pct") == 1


# ---- with_ratio -------------------------------------------------------------


def test_with_ratio_divides_by_baseline_column():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "USA", {"2020": 200.0})),
        ]
    )

    result = with_ratio(table, baseline_key="USA")

    assert result.value_at("2020", "AFG__ratio_to_USA") == pytest.approx(0.05)
    assert all(c.key != "USA__ratio_to_USA" for c in result.columns)


def test_with_ratio_does_not_duplicate_columns_when_called_twice():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "USA", {"2020": 200.0})),
        ]
    )

    once = with_ratio(table, baseline_key="USA")
    twice = with_ratio(once, baseline_key="USA")

    keys = [c.key for c in twice.columns if c.derived]
    assert keys.count("AFG__ratio_to_USA") == 1


def test_with_ratio_unknown_baseline_raises():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])

    with pytest.raises(ValueError):
        with_ratio(table, baseline_key="NOPE")


# ---- with_rank --------------------------------------------------------------


def test_with_rank_orders_columns_by_value_descending():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    col_c = ComparisonColumn(key="LUX", label="LUX", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "USA", {"2020": 300.0})),
            (col_c, _series("POP", "LUX", {"2020": 1.0})),
        ]
    )

    result = with_rank(table)

    assert result.value_at("2020", "USA__rank") == 1.0
    assert result.value_at("2020", "AFG__rank") == 2.0
    assert result.value_at("2020", "LUX__rank") == 3.0


def test_with_rank_does_not_duplicate_columns_when_called_twice():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "USA", {"2020": 300.0})),
        ]
    )

    once = with_rank(table)
    twice = with_rank(once)

    keys = [c.key for c in twice.columns if c.derived]
    assert keys.count("AFG__rank") == 1


def test_with_rank_skips_missing_values():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    col_b = ComparisonColumn(key="USA", label="USA", attribution=None)
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "USA", {"2019": 300.0})),  # no 2020 value
        ]
    )

    result = with_rank(table)

    assert result.value_at("2020", "AFG__rank") == 1.0
    assert result.value_at("2020", "USA__rank") is None


# ---- as_dict / attribution preserved ----------------------------------------


def test_as_dict_keeps_per_column_attribution_and_flags_derived():
    provider = LookupProvider("FAKE", {("POP", "AFG"): _series("POP", "AFG", {"2020": 10.0})})
    engine = QueryEngine({"FAKE": provider})
    table = with_growth(compare_across_countries(engine, "FAKE", "POP", ["AFG"]))

    payload = table.as_dict()
    base_col = next(c for c in payload["columns"] if c["key"] == "AFG")
    derived_col = next(c for c in payload["columns"] if c["key"] == "AFG__yoy_growth_pct")

    assert base_col["derived"] is False
    assert base_col["attribution"]["source_id"] == "FAKE"
    assert derived_col["derived"] is True
    assert derived_col["attribution"] is None
    assert payload["rows"] == [{"period": "2020", "AFG": 10.0, "AFG__yoy_growth_pct": None}]

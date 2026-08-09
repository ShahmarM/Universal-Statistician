"""Tests for compose.py's Phase 9 transformations: absolute/pp change,
CAGR, cumulative growth, index rebasing, moving average, difference,
share, per-capita, sum/average/weighted-average, and the frequency-neutral
with_period_over_period_growth. with_growth/with_ratio/with_rank's own
tests (including lineage) stay in test_compose.py.
"""

from __future__ import annotations

import pytest

from universal_statistician.core.compose import (
    ComparisonColumn,
    build_comparison,
    with_absolute_change,
    with_average,
    with_cagr,
    with_cumulative_growth,
    with_difference,
    with_index,
    with_moving_average,
    with_per_capita,
    with_period_over_period_growth,
    with_pp_change,
    with_share,
    with_sum,
    with_weighted_average,
)

from .helpers import make_series as _series


def _table(**series_by_key: dict[str, float | None]) -> "ComparisonTable":  # noqa: F821
    columns = [
        (ComparisonColumn(key=key, label=key, attribution=None), _series("X", key, obs))
        for key, obs in series_by_key.items()
    ]
    return build_comparison(columns)


# ---- with_absolute_change / with_pp_change ---------------------------------


def test_with_absolute_change_computes_level_difference():
    table = _table(AFG={"2019": 100.0, "2020": 130.0})
    result = with_absolute_change(table)

    col = next(c for c in result.columns if c.key == "AFG__abs_change")
    assert col.formula
    assert col.input_series == ("AFG",)
    assert result.value_at("2020", "AFG__abs_change") == pytest.approx(30.0)
    assert result.value_at("2019", "AFG__abs_change") is None


def test_with_pp_change_is_distinct_key_from_absolute_change():
    table = _table(AFG={"2019": 5.0, "2020": 7.5})
    result = with_pp_change(table)

    assert result.value_at("2020", "AFG__pp_change") == pytest.approx(2.5)
    assert any(c.key == "AFG__pp_change" for c in result.columns)


# ---- with_period_over_period_growth ----------------------------------------


def test_with_period_over_period_growth_matches_with_growth_math():
    table = _table(AFG={"2019": 100.0, "2020": 110.0})
    result = with_period_over_period_growth(table)

    assert result.value_at("2020", "AFG__period_over_period_growth_pct") == pytest.approx(10.0)


# ---- with_cagr / with_cumulative_growth ------------------------------------


def test_with_cagr_computes_compound_annual_growth_rate():
    table = _table(AFG={"2015": 100.0, "2020": 161.05})
    result = with_cagr(table)

    # (161.05/100)**(1/5) - 1 -> 10%
    assert result.value_at("2020", "AFG__cagr_pct") == pytest.approx(10.0, abs=0.01)
    assert result.value_at("2015", "AFG__cagr_pct") is None  # recorded at end period only


def test_with_cagr_respects_explicit_period_bounds():
    table = _table(AFG={"2010": 50.0, "2015": 100.0, "2020": 161.05})
    result = with_cagr(table, start_period="2015", end_period="2020")

    assert result.value_at("2020", "AFG__cagr_pct") == pytest.approx(10.0, abs=0.01)


def test_with_cagr_rejects_non_annual_periods():
    table = _table(AFG={"2020M01": 100.0, "2020M02": 110.0})
    with pytest.raises(ValueError, match="4-digit year"):
        with_cagr(table)


def test_with_cumulative_growth_computes_total_percent_change():
    table = _table(AFG={"2015": 100.0, "2020": 150.0})
    result = with_cumulative_growth(table)

    assert result.value_at("2020", "AFG__cumulative_growth_pct") == pytest.approx(50.0)


# ---- with_index -------------------------------------------------------------


def test_with_index_rebases_to_100_at_base_period():
    table = _table(AFG={"2015": 50.0, "2020": 75.0})
    result = with_index(table, base_period="2015")

    assert result.value_at("2015", "AFG__index") == pytest.approx(100.0)
    assert result.value_at("2020", "AFG__index") == pytest.approx(150.0)


def test_with_index_raises_when_base_period_missing():
    table = _table(AFG={"2020": 75.0})
    with pytest.raises(ValueError, match="base_period"):
        with_index(table, base_period="2015")


# ---- with_moving_average -----------------------------------------------------


def test_with_moving_average_computes_mean_of_recent_periods():
    table = _table(AFG={"2018": 10.0, "2019": 20.0, "2020": 30.0})
    result = with_moving_average(table, window=2)

    assert result.value_at("2019", "AFG__ma2") == pytest.approx(15.0)
    assert result.value_at("2020", "AFG__ma2") == pytest.approx(25.0)
    assert result.value_at("2018", "AFG__ma2") is None


def test_with_moving_average_rejects_window_below_two():
    table = _table(AFG={"2020": 1.0})
    with pytest.raises(ValueError):
        with_moving_average(table, window=1)


# ---- with_difference ----------------------------------------------------------


def test_with_difference_subtracts_two_columns():
    table = _table(AFG={"2020": 10.0}, USA={"2020": 4.0})
    result = with_difference(table, "AFG", "USA")

    assert result.value_at("2020", "AFG__minus_USA") == pytest.approx(6.0)
    col = next(c for c in result.columns if c.key == "AFG__minus_USA")
    assert col.input_series == ("AFG", "USA")


def test_with_difference_unknown_column_raises():
    table = _table(AFG={"2020": 10.0})
    with pytest.raises(ValueError):
        with_difference(table, "AFG", "NOPE")


# ---- with_share / with_per_capita ----------------------------------------------


def test_with_share_computes_percentage_of_total():
    table = _table(OIL={"2020": 30.0}, TOTAL={"2020": 100.0})
    result = with_share(table, total_key="TOTAL")

    assert result.value_at("2020", "OIL__share_of_TOTAL") == pytest.approx(30.0)


def test_with_per_capita_divides_by_population_column():
    table = _table(GDP={"2020": 1000.0}, POP={"2020": 10.0})
    result = with_per_capita(table, population_key="POP")

    assert result.value_at("2020", "GDP__per_capita") == pytest.approx(100.0)


# ---- with_sum / with_average / with_weighted_average ---------------------------


def test_with_sum_adds_listed_columns_when_all_present():
    table = _table(A={"2020": 10.0}, B={"2020": 5.0})
    result = with_sum(table, ["A", "B"])

    assert result.value_at("2020", "sum") == pytest.approx(15.0)


def test_with_sum_skips_period_with_a_missing_column():
    table = _table(A={"2020": 10.0, "2021": 1.0}, B={"2020": 5.0})
    result = with_sum(table, ["A", "B"])

    assert result.value_at("2020", "sum") == pytest.approx(15.0)
    assert result.value_at("2021", "sum") is None


def test_with_average_computes_unweighted_mean():
    table = _table(A={"2020": 10.0}, B={"2020": 20.0})
    result = with_average(table, ["A", "B"])

    assert result.value_at("2020", "average") == pytest.approx(15.0)


def test_with_weighted_average_uses_explicit_weights():
    table = _table(A={"2020": 10.0}, B={"2020": 20.0})
    result = with_weighted_average(table, {"A": 3.0, "B": 1.0})

    # (10*3 + 20*1) / 4 = 12.5
    assert result.value_at("2020", "weighted_average") == pytest.approx(12.5)


def test_with_weighted_average_rejects_zero_total_weight():
    table = _table(A={"2020": 10.0}, B={"2020": 20.0})
    with pytest.raises(ValueError, match="zero"):
        with_weighted_average(table, {"A": 1.0, "B": -1.0})


def test_aggregation_functions_are_idempotent():
    table = _table(A={"2020": 10.0}, B={"2020": 20.0})
    once = with_sum(table, ["A", "B"])
    twice = with_sum(once, ["A", "B"])

    assert [c.key for c in twice.columns].count("sum") == 1

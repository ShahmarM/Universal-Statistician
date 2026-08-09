from __future__ import annotations

from datetime import datetime, timezone

import pytest

from universal_statistician.core.compose import (
    ComparisonColumn,
    build_comparison,
    with_ratio,
)
from universal_statistician.core.models import Attribution, Observation, SeriesResult
from universal_statistician.core.validation import (
    ValidationStatus,
    validate_series,
    validate_table,
)

from .helpers import make_series as _series


def _attribution(source_id="FAKE") -> Attribution:
    return Attribution(
        source_id=source_id,
        source_name=f"Fake {source_id}",
        dataset_id="FAKE_DS",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---- validate_series ----------------------------------------------------------


def test_validate_series_passes_for_a_clean_series():
    series = _series("POP", "AFG", {"2019": 10.0, "2020": 11.0})
    result = validate_series(series)

    assert result.status == ValidationStatus.PASS
    assert result.ok


def test_validate_series_warns_on_duplicate_periods():
    series = SeriesResult(
        indicator_id="POP",
        ref_area="AFG",
        frequency="A",
        observations=(Observation("2020", 10.0), Observation("2020", 11.0)),
        attribution=_attribution(),
    )
    result = validate_series(series)

    assert result.status == ValidationStatus.WARNING
    assert any(f.check == "duplicate_observations" for f in result.findings)


def test_validate_series_fails_on_nan_value():
    series = SeriesResult(
        indicator_id="POP",
        ref_area="AFG",
        frequency="A",
        observations=(Observation("2020", float("nan")),),
        attribution=_attribution(),
    )
    result = validate_series(series)

    assert result.status == ValidationStatus.FAIL
    assert not result.ok
    assert any(f.check == "nan_value" for f in result.findings)


def test_validate_series_warns_on_empty_result():
    series = SeriesResult(
        indicator_id="POP", ref_area="AFG", frequency="A", observations=(), attribution=_attribution()
    )
    result = validate_series(series)

    assert any(f.check == "no_observations" for f in result.findings)


# ---- validate_table: citations ------------------------------------------------


def test_validate_table_fails_when_a_base_column_has_no_attribution():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])
    # build_comparison keeps whatever attribution the column was given —
    # simulate a column constructed without one.
    table = table.__class__(columns=(col,), values=table.values)

    result = validate_table(table)

    assert result.status == ValidationStatus.FAIL
    assert not result.ok
    assert any(f.check == "citations_exist" for f in result.findings)


def test_validate_table_passes_for_a_clean_comparison():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2019": 10.0, "2020": 11.0})),
            (col_b, _series("POP", "USA", {"2019": 300.0, "2020": 310.0})),
        ]
    )

    result = validate_table(table)

    assert result.status == ValidationStatus.PASS
    assert result.ok


# ---- validate_table: unit / frequency consistency ------------------------------


def test_validate_table_warns_on_differing_known_frequencies():
    col_a = ComparisonColumn(key="A", label="A", attribution=_attribution(), frequency="A")
    col_b = ComparisonColumn(key="B", label="B", attribution=_attribution(), frequency="M")
    table = build_comparison(
        [(col_a, _series("X", "A", {"2020": 1.0})), (col_b, _series("X", "B", {"2020": 1.0}))]
    )

    result = validate_table(table)

    assert result.status == ValidationStatus.WARNING
    assert any(f.check == "frequency_consistency" for f in result.findings)


def test_validate_table_does_not_warn_when_frequency_is_unknown():
    col_a = ComparisonColumn(key="A", label="A", attribution=_attribution(), frequency="A")
    col_b = ComparisonColumn(key="B", label="B", attribution=_attribution(), frequency=None)
    table = build_comparison(
        [(col_a, _series("X", "A", {"2020": 1.0})), (col_b, _series("X", "B", {"2020": 1.0}))]
    )

    result = validate_table(table)

    assert not any(f.check == "frequency_consistency" for f in result.findings)


def test_validate_table_warns_on_differing_known_units():
    col_a = ComparisonColumn(key="A", label="A", attribution=_attribution(), unit="USD")
    col_b = ComparisonColumn(key="B", label="B", attribution=_attribution(), unit="EUR")
    table = build_comparison(
        [(col_a, _series("X", "A", {"2020": 1.0})), (col_b, _series("X", "B", {"2020": 1.0}))]
    )

    result = validate_table(table)

    assert any(f.check == "unit_consistency" for f in result.findings)


# ---- validate_table: requested geographies / periods ---------------------------


def test_validate_table_warns_when_a_requested_geography_is_missing():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])

    result = validate_table(table, requested_geographies=("AFG", "USA"))

    assert any(
        f.check == "requested_geographies_returned" and "USA" in f.message for f in result.findings
    )


def test_validate_table_warns_when_requested_period_range_not_covered():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2010": 10.0}))])

    result = validate_table(table, requested_start_period="2015", requested_end_period="2020")

    assert any(f.check == "requested_periods_returned" for f in result.findings)


# ---- validate_table: gaps -------------------------------------------------------


def test_validate_table_warns_on_a_gap_in_annual_coverage():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2015": 1.0, "2016": 1.0, "2019": 1.0}))])

    result = validate_table(table)

    assert any(f.check == "no_unexpected_gaps" for f in result.findings)


def test_validate_table_does_not_check_gaps_for_non_annual_periods():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2020M01": 1.0, "2020M06": 1.0}))])

    result = validate_table(table)

    assert not any(f.check == "no_unexpected_gaps" for f in result.findings)


# ---- validate_table: derived-column lineage -------------------------------------


def test_validate_table_passes_lineage_check_for_real_transformations():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = with_ratio(
        build_comparison(
            [
                (col_a, _series("POP", "AFG", {"2020": 10.0})),
                (col_b, _series("POP", "USA", {"2020": 200.0})),
            ]
        ),
        baseline_key="USA",
    )

    result = validate_table(table)

    assert not any(f.check == "formula_correctness" for f in result.findings)


def test_validate_table_fails_when_a_derived_column_has_broken_lineage():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])
    broken = ComparisonColumn(
        key="AFG__bad",
        label="bad",
        attribution=None,
        derived=True,
        formula="whatever",
        input_series=("DOES_NOT_EXIST",),
    )
    table = table.__class__(columns=table.columns + (broken,), values=table.values)

    result = validate_table(table)

    assert result.status == ValidationStatus.FAIL
    assert any(f.check == "formula_correctness" for f in result.findings)


def test_validate_table_fails_when_a_derived_column_has_no_input_series():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])
    broken = ComparisonColumn(
        key="AFG__bad", label="bad", attribution=None, derived=True, formula="whatever"
    )
    table = table.__class__(columns=table.columns + (broken,), values=table.values)

    result = validate_table(table)

    assert result.status == ValidationStatus.FAIL


def test_validation_result_as_dict_is_json_friendly():
    series = _series("POP", "AFG", {"2020": 10.0})
    result = validate_series(series)

    payload = result.as_dict()
    assert payload["status"] == "PASS"
    assert payload["findings"][0]["status"] == "PASS"

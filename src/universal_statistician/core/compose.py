"""Derived comparison tables built from multiple get_series() calls.

Base columns keep the Attribution of the fetch they came from; derived
columns carry `derived=True`, `formula`, and `input_series` instead of an
Attribution, so computed numbers are never mistaken for source-reported
ones and stay traceable to their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import Attribution, SeriesResult, StatisticalSemantics

#: (input_column_key, input_period) an output cell depended on.
CellDependency = tuple[str, str]


@dataclass(frozen=True)
class ComparisonColumn:
    key: str
    label: str
    attribution: Attribution | None
    derived: bool = False
    #: How the column was computed; None for a base (fetched) column.
    formula: str | None = None
    #: Column keys this one was computed from; None for a base column.
    input_series: tuple[str, ...] | None = None
    unit: str | None = None
    frequency: str | None = None
    #: Explicit series identity for base columns (key/label alone conflate
    #: indicator vs. geography); None for derived columns.
    indicator_id: str | None = None
    ref_area: str | None = None
    semantics: StatisticalSemantics | None = None


@dataclass(frozen=True)
class ComparisonTable:
    columns: tuple[ComparisonColumn, ...]
    #: (period, column_key) -> value
    values: dict[tuple[str, str], float | None]
    #: (period, column_key) -> exact input cells it was computed from.
    #: Recorded by each with_*() at compute time (input_series is per-column,
    #: too coarse for per-period lineage). Set only for derived cells; not
    #: part of as_dict()'s JSON shape.
    cell_dependencies: dict[tuple[str, str], tuple[CellDependency, ...]] = field(default_factory=dict)

    def periods(self) -> tuple[str, ...]:
        # Memoized: tables are treated as immutable (every with_*() builds a
        # new one) and this is called from per-column loops.
        cached = self.__dict__.get("_periods")
        if cached is None:
            cached = tuple(sorted({period for period, _column_key in self.values}))
            object.__setattr__(self, "_periods", cached)
        return cached

    def value_at(self, period: str, column_key: str) -> float | None:
        return self.values.get((period, column_key))

    def column(self, key: str) -> ComparisonColumn:
        for c in self.columns:
            if c.key == key:
                return c
        raise ValueError(f"Unknown column {key!r}")

    def as_dict(self) -> dict:
        return {
            "columns": [
                {
                    "key": c.key,
                    "label": c.label,
                    "derived": c.derived,
                    "attribution": c.attribution.as_dict() if c.attribution else None,
                    "formula": c.formula,
                    "input_series": list(c.input_series) if c.input_series is not None else None,
                    "unit": c.unit,
                    "frequency": c.frequency,
                    "indicator_id": c.indicator_id,
                    "ref_area": c.ref_area,
                    "semantics": c.semantics.as_dict() if c.semantics is not None else None,
                }
                for c in self.columns
            ],
            "rows": [
                {
                    "period": period,
                    **{c.key: self.value_at(period, c.key) for c in self.columns},
                }
                for period in self.periods()
            ],
        }


def build_comparison(columns: list[tuple[ComparisonColumn, SeriesResult]]) -> ComparisonTable:
    """Assemble a table from already-fetched (column, SeriesResult) pairs;
    pure and network-free."""
    values: dict[tuple[str, str], float | None] = {}
    for column, series in columns:
        for obs in series.observations:
            values[(obs.period, column.key)] = obs.value

    return ComparisonTable(columns=tuple(c for c, _ in columns), values=values)


def compare_across_countries(
    engine: QueryEngine,
    source_id: str,
    indicator_id: str,
    ref_areas: list[str],
    *,
    start_period: str | None = None,
    end_period: str | None = None,
) -> ComparisonTable:
    """One indicator, several countries/areas — country × year table."""
    columns = []
    for ref_area in ref_areas:
        series = engine.get_series(
            source_id, indicator_id, ref_area, start_period=start_period, end_period=end_period
        )
        column = ComparisonColumn(
            key=ref_area,
            label=ref_area,
            attribution=series.attribution,
            unit=series.unit,
            frequency=series.frequency,
            indicator_id=indicator_id,
            ref_area=ref_area,
            semantics=series.semantics,
        )
        columns.append((column, series))
    return build_comparison(columns)


def compare_across_indicators(
    engine: QueryEngine,
    source_id: str,
    indicator_ids: list[str],
    ref_area: str,
    *,
    start_period: str | None = None,
    end_period: str | None = None,
) -> ComparisonTable:
    """Several indicators, one country/area — indicator × year table."""
    columns = []
    for indicator_id in indicator_ids:
        series = engine.get_series(
            source_id, indicator_id, ref_area, start_period=start_period, end_period=end_period
        )
        column = ComparisonColumn(
            key=indicator_id,
            label=indicator_id,
            attribution=series.attribution,
            unit=series.unit,
            frequency=series.frequency,
            indicator_id=indicator_id,
            ref_area=ref_area,
            semantics=series.semantics,
        )
        columns.append((column, series))
    return build_comparison(columns)


def _base_columns(table: ComparisonTable) -> list[ComparisonColumn]:
    return [c for c in table.columns if not c.derived]


def _periods_with_values(table: ComparisonTable, key: str) -> list[str]:
    return sorted(p for p in table.periods() if table.value_at(p, key) is not None)


def _with_adjacent_delta(
    table: ComparisonTable, *, suffix: str, label_suffix: str, formula: str, compute
) -> ComparisonTable:
    """Add a `{key}{suffix}` derived column per base column, computed from
    each adjacent (previous, current) pair of periods with values.
    `compute(prev, curr)` returns the cell value, or None to skip."""
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in _base_columns(table):
        new_key = f"{column.key}{suffix}"
        if new_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=new_key,
                label=f"{column.label}: {label_suffix}",
                attribution=None,
                derived=True,
                formula=formula,
                input_series=(column.key,),
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            value = compute(table.value_at(previous, column.key), table.value_at(current, column.key))
            if value is not None:
                new_values[(current, new_key)] = value
                new_dependencies[(current, new_key)] = ((column.key, previous), (column.key, current))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def _relative_growth(prev: float | None, curr: float) -> float | None:
    # None for growth-from-zero (undefined).
    return (curr - prev) / prev * 100 if prev else None


def with_growth(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__yoy_growth_pct` column per base column: percent change
    from the previous period with a value. "yoy" is historical naming — the
    math is period-over-period; see with_period_over_period_growth for the
    frequency-neutral name."""
    return _with_adjacent_delta(
        table, suffix="__yoy_growth_pct", label_suffix="YoY growth %",
        formula="(current - previous) / previous * 100", compute=_relative_growth,
    )


def with_period_over_period_growth(table: ComparisonTable) -> ComparisonTable:
    """with_growth() under a frequency-neutral key/label
    (`{key}__period_over_period_growth_pct`)."""
    return _with_adjacent_delta(
        table, suffix="__period_over_period_growth_pct", label_suffix="period-over-period growth %",
        formula="(current - previous) / previous * 100", compute=_relative_growth,
    )


def with_absolute_change(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__abs_change` column per base column: current minus
    previous period (level change, not %)."""
    return _with_adjacent_delta(
        table, suffix="__abs_change", label_suffix="absolute change",
        formula="current - previous", compute=lambda prev, curr: curr - prev,
    )


def with_pp_change(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__pp_change` column per base column: current minus
    previous, in percentage points — the correct delta for values that are
    already rates (subtract, don't divide)."""
    return _with_adjacent_delta(
        table, suffix="__pp_change", label_suffix="change (pp)",
        formula="current - previous (percentage points)", compute=lambda prev, curr: curr - prev,
    )


def _year(period: str) -> int:
    if len(period) != 4 or not period.isdigit():
        raise ValueError(
            f"Period {period!r} is not a 4-digit year; CAGR needs annual periods "
            "(e.g. '2015'), not sub-annual ones like '2015M01' or '2015Q1'."
        )
    return int(period)


def with_cagr(
    table: ComparisonTable, *, start_period: str | None = None, end_period: str | None = None
) -> ComparisonTable:
    """Add a `{key}__cagr_pct` column per base column: compound annual
    growth rate (%) over the range, recorded at the end period only.
    Raises ValueError for non-annual periods — an annual rate from
    sub-annual periods would be silently wrong."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        cagr_key = f"{column.key}__cagr_pct"
        if cagr_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        if not periods_with_values:
            continue
        start = start_period or periods_with_values[0]
        end = end_period or periods_with_values[-1]
        start_value = table.value_at(start, column.key)
        end_value = table.value_at(end, column.key)

        new_columns.append(
            ComparisonColumn(
                key=cagr_key,
                label=f"{column.label}: CAGR % ({start}-{end})",
                attribution=None,
                derived=True,
                formula="((end / start) ** (1 / years) - 1) * 100",
                input_series=(column.key,),
            )
        )
        if start_value and end_value is not None:
            years = _year(end) - _year(start)
            if years > 0:
                new_values[(end, cagr_key)] = ((end_value / start_value) ** (1 / years) - 1) * 100
                new_dependencies[(end, cagr_key)] = ((column.key, start), (column.key, end))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_cumulative_growth(
    table: ComparisonTable, *, start_period: str | None = None, end_period: str | None = None
) -> ComparisonTable:
    """Add a `{key}__cumulative_growth_pct` column per base column: total
    percent change over the range, recorded at the end period only."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        cum_key = f"{column.key}__cumulative_growth_pct"
        if cum_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        if not periods_with_values:
            continue
        start = start_period or periods_with_values[0]
        end = end_period or periods_with_values[-1]
        start_value = table.value_at(start, column.key)
        end_value = table.value_at(end, column.key)

        new_columns.append(
            ComparisonColumn(
                key=cum_key,
                label=f"{column.label}: cumulative growth % ({start}-{end})",
                attribution=None,
                derived=True,
                formula="(end / start - 1) * 100",
                input_series=(column.key,),
            )
        )
        if start_value and end_value is not None:
            new_values[(end, cum_key)] = (end_value / start_value - 1) * 100
            new_dependencies[(end, cum_key)] = ((column.key, start), (column.key, end))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_index(table: ComparisonTable, base_period: str) -> ComparisonTable:
    """Add a `{key}__index` column per base column, rebased so base_period
    = 100. Raises ValueError if a base column has no value at base_period."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        index_key = f"{column.key}__index"
        if index_key in existing_keys:
            continue

        base_value = table.value_at(base_period, column.key)
        if not base_value:
            raise ValueError(
                f"Cannot index {column.key!r}: no non-zero value at base_period {base_period!r}"
            )

        new_columns.append(
            ComparisonColumn(
                key=index_key,
                label=f"{column.label}: index ({base_period}=100)",
                attribution=None,
                derived=True,
                formula=f"value / value[{base_period}] * 100",
                input_series=(column.key,),
            )
        )
        for period in table.periods():
            value = table.value_at(period, column.key)
            if value is not None:
                new_values[(period, index_key)] = value / base_value * 100
                new_dependencies[(period, index_key)] = (
                    (column.key, base_period),
                    (column.key, period),
                )

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_moving_average(table: ComparisonTable, window: int) -> ComparisonTable:
    """Add a `{key}__ma{window}` column per base column: mean of the
    `window` most recent periods with a value (gaps allowed)."""
    if window < 2:
        raise ValueError(f"window must be at least 2, got {window}")

    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        ma_key = f"{column.key}__ma{window}"
        if ma_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=ma_key,
                label=f"{column.label}: {window}-period moving average",
                attribution=None,
                derived=True,
                formula=f"mean of the {window} most recent periods with a value",
                input_series=(column.key,),
            )
        )
        for i in range(window - 1, len(periods_with_values)):
            window_periods = periods_with_values[i - window + 1 : i + 1]
            values = [table.value_at(p, column.key) for p in window_periods]
            new_values[(periods_with_values[i], ma_key)] = sum(values) / window
            new_dependencies[(periods_with_values[i], ma_key)] = tuple(
                (column.key, p) for p in window_periods
            )

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_difference(
    table: ComparisonTable, key_a: str, key_b: str, *, result_key: str | None = None
) -> ComparisonTable:
    """Add one column, `{key_a}__minus_{key_b}` by default: key_a minus
    key_b for every period both are present."""
    known_keys = {c.key for c in table.columns}
    if key_a not in known_keys:
        raise ValueError(f"Unknown column {key_a!r}")
    if key_b not in known_keys:
        raise ValueError(f"Unknown column {key_b!r}")

    diff_key = result_key or f"{key_a}__minus_{key_b}"
    if diff_key in known_keys:
        return table

    label_a = table.column(key_a).label
    label_b = table.column(key_b).label
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    for period in table.periods():
        value_a = table.value_at(period, key_a)
        value_b = table.value_at(period, key_b)
        if value_a is not None and value_b is not None:
            new_values[(period, diff_key)] = value_a - value_b
            new_dependencies[(period, diff_key)] = ((key_a, period), (key_b, period))

    new_column = ComparisonColumn(
        key=diff_key,
        label=f"{label_a} - {label_b}",
        attribution=None,
        derived=True,
        formula="value_a - value_b",
        input_series=(key_a, key_b),
    )
    return ComparisonTable(
        columns=table.columns + (new_column,), values=new_values, cell_dependencies=new_dependencies
    )


def with_share_pair(
    table: ComparisonTable, numerator_key: str, denominator_key: str, *, result_key: str | None = None
) -> ComparisonTable:
    """Add one column: numerator as a percentage of denominator, for every
    period both are present. Two-column counterpart of with_share()."""
    known_keys = {c.key for c in table.columns}
    if numerator_key not in known_keys:
        raise ValueError(f"Unknown column {numerator_key!r}")
    if denominator_key not in known_keys:
        raise ValueError(f"Unknown column {denominator_key!r}")

    share_key = result_key or f"{numerator_key}__share_of_{denominator_key}"
    if share_key in known_keys:
        return table

    label_num = table.column(numerator_key).label
    label_den = table.column(denominator_key).label
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    for period in table.periods():
        value = table.value_at(period, numerator_key)
        total = table.value_at(period, denominator_key)
        if value is not None and total:
            new_values[(period, share_key)] = value / total * 100
            new_dependencies[(period, share_key)] = ((numerator_key, period), (denominator_key, period))

    new_column = ComparisonColumn(
        key=share_key,
        label=f"{label_num}: share of {label_den} (%)",
        attribution=None,
        derived=True,
        formula="numerator / denominator * 100",
        input_series=(numerator_key, denominator_key),
    )
    return ComparisonTable(
        columns=table.columns + (new_column,), values=new_values, cell_dependencies=new_dependencies
    )


def with_per_capita_pair(
    table: ComparisonTable, numerator_key: str, denominator_key: str, *, result_key: str | None = None
) -> ComparisonTable:
    """Add one column: numerator divided by denominator, for every period
    both are present. Two-column counterpart of with_per_capita()."""
    known_keys = {c.key for c in table.columns}
    if numerator_key not in known_keys:
        raise ValueError(f"Unknown column {numerator_key!r}")
    if denominator_key not in known_keys:
        raise ValueError(f"Unknown column {denominator_key!r}")

    pc_key = result_key or f"{numerator_key}__per_capita"
    if pc_key in known_keys:
        return table

    label_num = table.column(numerator_key).label
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    for period in table.periods():
        value = table.value_at(period, numerator_key)
        denominator = table.value_at(period, denominator_key)
        if value is not None and denominator:
            new_values[(period, pc_key)] = value / denominator
            new_dependencies[(period, pc_key)] = ((numerator_key, period), (denominator_key, period))

    new_column = ComparisonColumn(
        key=pc_key,
        label=f"{label_num} per capita",
        attribution=None,
        derived=True,
        formula="numerator / denominator",
        input_series=(numerator_key, denominator_key),
    )
    return ComparisonTable(
        columns=table.columns + (new_column,), values=new_values, cell_dependencies=new_dependencies
    )


def with_index_column(
    table: ComparisonTable,
    column_key: str,
    base_period: str,
    *,
    base_value: float = 100.0,
    result_key: str | None = None,
) -> ComparisonTable:
    """Add one column: column_key rebased so base_period = base_value
    (default 100). Single-column counterpart of with_index() — rebasing
    every base column would be wrong when the table also holds unrelated
    concepts."""
    known_keys = {c.key for c in table.columns}
    if column_key not in known_keys:
        raise ValueError(f"Unknown column {column_key!r}")

    index_key = result_key or f"{column_key}__index"
    if index_key in known_keys:
        return table

    base = table.value_at(base_period, column_key)
    if not base:
        raise ValueError(
            f"Cannot index {column_key!r}: no non-zero value at base_period {base_period!r}"
        )

    label = table.column(column_key).label
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    for period in table.periods():
        value = table.value_at(period, column_key)
        if value is not None:
            new_values[(period, index_key)] = value / base * base_value
            new_dependencies[(period, index_key)] = ((column_key, base_period), (column_key, period))

    new_column = ComparisonColumn(
        key=index_key,
        label=f"{label}: index ({base_period}={base_value:g})",
        attribution=None,
        derived=True,
        formula=f"value / value[{base_period}] * {base_value:g}",
        input_series=(column_key,),
    )
    return ComparisonTable(
        columns=table.columns + (new_column,), values=new_values, cell_dependencies=new_dependencies
    )


def with_share(table: ComparisonTable, total_key: str) -> ComparisonTable:
    """Add a `{key}__share_of_{total_key}` derived column per other base
    column: that column's value as a percentage of the total column's value."""
    base_columns = _base_columns(table)
    if total_key not in {c.key for c in base_columns}:
        raise ValueError(f"Unknown total column {total_key!r}")

    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        if column.key == total_key:
            continue
        share_key = f"{column.key}__share_of_{total_key}"
        if share_key in existing_keys:
            continue
        new_columns.append(
            ComparisonColumn(
                key=share_key,
                label=f"{column.label}: share of {total_key} (%)",
                attribution=None,
                derived=True,
                formula="value / total * 100",
                input_series=(column.key, total_key),
            )
        )
        for period in table.periods():
            value = table.value_at(period, column.key)
            total_value = table.value_at(period, total_key)
            if value is not None and total_value:
                new_values[(period, share_key)] = value / total_value * 100
                new_dependencies[(period, share_key)] = ((column.key, period), (total_key, period))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_per_capita(table: ComparisonTable, population_key: str) -> ComparisonTable:
    """Add a `{key}__per_capita` column per other base column: value
    divided by the population column's value."""
    base_columns = _base_columns(table)
    if population_key not in {c.key for c in base_columns}:
        raise ValueError(f"Unknown population column {population_key!r}")

    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        if column.key == population_key:
            continue
        pc_key = f"{column.key}__per_capita"
        if pc_key in existing_keys:
            continue
        new_columns.append(
            ComparisonColumn(
                key=pc_key,
                label=f"{column.label} per capita",
                attribution=None,
                derived=True,
                formula="value / population",
                input_series=(column.key, population_key),
            )
        )
        for period in table.periods():
            value = table.value_at(period, column.key)
            population = table.value_at(period, population_key)
            if value is not None and population:
                new_values[(period, pc_key)] = value / population
                new_dependencies[(period, pc_key)] = ((column.key, period), (population_key, period))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_ratio(table: ComparisonTable, baseline_key: str) -> ComparisonTable:
    """Add a `{key}__ratio_to_{baseline_key}` derived column per other base
    column: that column's value divided by the baseline column's value, for
    every period both are present."""
    base_columns = _base_columns(table)
    if baseline_key not in {c.key for c in base_columns}:
        raise ValueError(f"Unknown baseline column {baseline_key!r}")

    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    existing_keys = {c.key for c in table.columns}
    new_columns = list(table.columns)

    for column in base_columns:
        if column.key == baseline_key:
            continue
        ratio_key = f"{column.key}__ratio_to_{baseline_key}"
        if ratio_key in existing_keys:
            continue
        new_columns.append(
            ComparisonColumn(
                key=ratio_key,
                label=f"{column.label} / {baseline_key}",
                attribution=None,
                derived=True,
                formula="value / baseline_value",
                input_series=(column.key, baseline_key),
            )
        )
        for period in table.periods():
            value = table.value_at(period, column.key)
            baseline_value = table.value_at(period, baseline_key)
            if value is not None and baseline_value:
                new_values[(period, ratio_key)] = value / baseline_value
                new_dependencies[(period, ratio_key)] = ((column.key, period), (baseline_key, period))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def _aggregate(
    table: ComparisonTable,
    keys: list[str],
    *,
    result_key: str,
    result_label: str,
    formula: str,
    combine,
) -> ComparisonTable:
    known_keys = {c.key for c in table.columns}
    unknown = [k for k in keys if k not in known_keys]
    if unknown:
        raise ValueError(f"Unknown column(s): {unknown}")
    if result_key in known_keys:
        return table

    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    for period in table.periods():
        values = [table.value_at(period, k) for k in keys]
        if all(v is not None for v in values):
            new_values[(period, result_key)] = combine(values)
            new_dependencies[(period, result_key)] = tuple((k, period) for k in keys)

    new_column = ComparisonColumn(
        key=result_key,
        label=result_label,
        attribution=None,
        derived=True,
        formula=formula,
        input_series=tuple(keys),
    )
    return ComparisonTable(
        columns=table.columns + (new_column,), values=new_values, cell_dependencies=new_dependencies
    )


def with_sum(
    table: ComparisonTable, keys: list[str], *, result_key: str = "sum", result_label: str = "Sum"
) -> ComparisonTable:
    """Add one column: sum of the listed columns, only for periods where
    every listed column has a value (no silent undercount)."""
    return _aggregate(
        table, keys, result_key=result_key, result_label=result_label,
        formula="sum(values)", combine=sum,
    )


def with_average(
    table: ComparisonTable,
    keys: list[str],
    *,
    result_key: str = "average",
    result_label: str = "Average",
) -> ComparisonTable:
    """Add one column: unweighted mean of the listed columns, only for
    periods where every listed column has a value."""
    return _aggregate(
        table, keys, result_key=result_key, result_label=result_label,
        formula="mean(values)", combine=lambda values: sum(values) / len(values),
    )


def with_weighted_average(
    table: ComparisonTable,
    weights: dict[str, float],
    *,
    result_key: str = "weighted_average",
    result_label: str = "Weighted average",
) -> ComparisonTable:
    """Add one column: weighted mean using caller-supplied explicit weights
    (never inferred), only for periods where every column has a value."""
    keys = list(weights)
    total_weight = sum(weights.values())
    if total_weight == 0:
        raise ValueError("Weights must not sum to zero")

    def combine(values: list[float]) -> float:
        return sum(v * weights[k] for v, k in zip(values, keys)) / total_weight

    return _aggregate(
        table, keys, result_key=result_key, result_label=result_label,
        formula=f"sum(value_i * weight_i) / {total_weight} for weights={weights}",
        combine=combine,
    )


def with_rank(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__rank` column per base column: rank among all base
    columns per period (1 = highest). Only meaningful when base columns are
    commensurable."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    all_base_keys = tuple(c.key for c in base_columns)
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns) + [
        ComparisonColumn(
            key=f"{c.key}__rank",
            label=f"{c.label}: rank",
            attribution=None,
            derived=True,
            formula="descending rank among base columns for the same period",
            input_series=all_base_keys,
        )
        for c in base_columns
        if f"{c.key}__rank" not in existing_keys
    ]

    for period in table.periods():
        scored = [
            (c.key, table.value_at(period, c.key))
            for c in base_columns
            if table.value_at(period, c.key) is not None
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        # Each rank depends only on columns that had a value this period.
        participating = tuple((k, period) for k, _v in scored)
        for rank, (key, _value) in enumerate(scored, start=1):
            new_values[(period, f"{key}__rank")] = float(rank)
            new_dependencies[(period, f"{key}__rank")] = participating

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)

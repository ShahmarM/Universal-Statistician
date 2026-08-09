"""Derived comparison tables built from multiple get_series() calls.

This is what makes the assistant answer "GDP per capita for X, Y, Z from
2015-2024" rather than only "one indicator, one country, one call" — the
capability the MVP scope was explicitly widened for. A ComparisonTable is
plain data (columns + period/column -> value cells); building one is
separated from computing derived columns (growth/ratio/rank/...) so each
step is independently testable and callers can mix and match.

Every base column keeps the Attribution of the get_series() call it came
from — cells from different countries or indicators must never be blended
without each one's own source staying visible. Derived columns carry no
Attribution of their own: they're computed here, not fetched from any
official source, and are labeled `derived=True` so a caller can never
mistake a computed number for one reported directly by a source. Every
derived column also carries `formula` (a short, human-readable description
of the calculation) and `input_series` (the column keys it was computed
from) — this project's task description (section 14/17) calls this
"lineage," and it is what lets a derived value be traced back to the exact
official observations behind it, the same way Attribution does for a base
column.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import Attribution, SeriesResult, StatisticalSemantics

#: One entry per (input_column_key, input_period) an output cell actually
#: depended on to compute its value.
CellDependency = tuple[str, str]


@dataclass(frozen=True)
class ComparisonColumn:
    key: str
    label: str
    attribution: Attribution | None
    derived: bool = False
    #: Short, human-readable description of how this column was computed —
    #: None for a base column (it wasn't computed, it was fetched).
    formula: str | None = None
    #: Column keys this one was computed from — None for a base column.
    input_series: tuple[str, ...] | None = None
    #: Carried from the fetching SeriesResult for a base column (None for a
    #: derived one) — core/validation.py's unit/frequency-consistency checks
    #: read these; see SeriesResult.unit's docstring on why unit is commonly
    #: None in practice today.
    unit: str | None = None
    frequency: str | None = None
    #: The indicator/series id and geography this base column's values came
    #: from — None for a derived column. `key`/`label` alone conflate these
    #: (compare_across_countries uses `key` for ref_area with one shared
    #: indicator; compare_across_indicators uses `key` for indicator_id with
    #: one shared ref_area), so core/provenance.py (Phase 10) needs these
    #: explicit to build a citation that names both, the way section 17's
    #: example does ("NY.GDP.PCAP.CD" + "Azerbaijan").
    indicator_id: str | None = None
    ref_area: str | None = None
    #: Structured semantics (Phase F) — see StatisticalSemantics. None for a
    #: derived column (computed here, not published with its own semantics)
    #: as well as for a base column whose source doesn't expose it.
    semantics: StatisticalSemantics | None = None


@dataclass(frozen=True)
class ComparisonTable:
    columns: tuple[ComparisonColumn, ...]
    #: (period, column_key) -> value
    values: dict[tuple[str, str], float | None]
    #: (period, column_key) -> the exact (input_column_key, input_period)
    #: pairs that specific cell was computed from — Phase E ("exact derived
    #: provenance"): recorded by each with_*() transformation at the moment
    #: it computes a value, never inferred afterward from formula text or
    #: ComparisonColumn.input_series (which only names *columns*, shared
    #: across every period of a derived column — too coarse for an
    #: operation like with_growth, where period P's value depends on
    #: different specific input periods than period Q's). Only set for
    #: derived cells; absent (not just empty) for a base cell. Internal to
    #: core/provenance.py's resolver — deliberately not part of
    #: ComparisonTable.as_dict()'s JSON shape (AskResult.provenance already
    #: carries the resolved chain for whatever cell was asked about).
    cell_dependencies: dict[tuple[str, str], tuple[CellDependency, ...]] = field(default_factory=dict)

    def periods(self) -> tuple[str, ...]:
        return tuple(sorted({period for period, _column_key in self.values}))

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
    """Assemble a table from already-fetched (column, SeriesResult) pairs.

    Pure and network-free: fetching lives in compare_across_countries() /
    compare_across_indicators() below, this just aligns results onto a shared
    period axis.
    """
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


def with_growth(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__yoy_growth_pct` derived column per base column: percent
    change from the previous period present in that column (chronological
    order by period string — true for every period format this project uses
    so far: "YYYY" and "YYYY-MM" both sort correctly as plain strings).

    Named "yoy" for historical reasons (see README's documented nuance): the
    math is period-over-period, not necessarily annual — accurate for a
    yearly table, but a monthly table's result is really month-over-month
    computed correctly, just under this label. New callers wanting an
    accurate label for non-annual data should use with_period_over_period_growth,
    which computes the identical value under a frequency-neutral name.
    """
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        growth_key = f"{column.key}__yoy_growth_pct"
        if growth_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=growth_key,
                label=f"{column.label}: YoY growth %",
                attribution=None,
                derived=True,
                formula="(current - previous) / previous * 100",
                input_series=(column.key,),
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            prev_value = table.value_at(previous, column.key)
            curr_value = table.value_at(current, column.key)
            if prev_value:  # skip growth-from-zero (undefined) and prev_value is None
                new_values[(current, growth_key)] = (curr_value - prev_value) / prev_value * 100
                new_dependencies[(current, growth_key)] = (
                    (column.key, previous),
                    (column.key, current),
                )

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_period_over_period_growth(table: ComparisonTable) -> ComparisonTable:
    """Same calculation as with_growth(), under an honest, frequency-neutral
    label and key (`{key}__period_over_period_growth_pct`) — for callers who
    want to avoid with_growth()'s "yoy" naming when the table isn't annual."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        growth_key = f"{column.key}__period_over_period_growth_pct"
        if growth_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=growth_key,
                label=f"{column.label}: period-over-period growth %",
                attribution=None,
                derived=True,
                formula="(current - previous) / previous * 100",
                input_series=(column.key,),
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            prev_value = table.value_at(previous, column.key)
            curr_value = table.value_at(current, column.key)
            if prev_value:
                new_values[(current, growth_key)] = (curr_value - prev_value) / prev_value * 100
                new_dependencies[(current, growth_key)] = (
                    (column.key, previous),
                    (column.key, current),
                )

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_absolute_change(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__abs_change` derived column per base column: current
    minus previous period present in that column (level change, not %)."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        change_key = f"{column.key}__abs_change"
        if change_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=change_key,
                label=f"{column.label}: absolute change",
                attribution=None,
                derived=True,
                formula="current - previous",
                input_series=(column.key,),
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            prev_value = table.value_at(previous, column.key)
            curr_value = table.value_at(current, column.key)
            new_values[(current, change_key)] = curr_value - prev_value
            new_dependencies[(current, change_key)] = ((column.key, previous), (column.key, current))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


def with_pp_change(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__pp_change` derived column per base column: current
    minus previous period, in percentage points — for columns whose values
    are themselves already a percentage/rate (e.g. an inflation rate or an
    unemployment rate), where subtracting is the correct comparison, not
    dividing (see with_growth for a %-of-value comparison instead)."""
    base_columns = _base_columns(table)
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_dependencies = dict(table.cell_dependencies)
    new_columns = list(table.columns)

    for column in base_columns:
        pp_key = f"{column.key}__pp_change"
        if pp_key in existing_keys:
            continue

        periods_with_values = _periods_with_values(table, column.key)
        new_columns.append(
            ComparisonColumn(
                key=pp_key,
                label=f"{column.label}: change (pp)",
                attribution=None,
                derived=True,
                formula="current - previous (percentage points)",
                input_series=(column.key,),
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            prev_value = table.value_at(previous, column.key)
            curr_value = table.value_at(current, column.key)
            new_values[(current, pp_key)] = curr_value - prev_value
            new_dependencies[(current, pp_key)] = ((column.key, previous), (column.key, current))

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)


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
    """Add a `{key}__cagr_pct` derived column per base column: compound
    annual growth rate (%) between the first/last available period (or the
    given start_period/end_period), recorded at the end period only — CAGR
    is a single number over a range, not a per-period series.

    Requires periods parseable as a 4-digit year (raises ValueError
    otherwise) — computing an *annual* rate from sub-annual periods without
    knowing how many periods make a year would silently produce a wrong
    number, which this project's validation principle refuses to do.
    """
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
    """Add a `{key}__cumulative_growth_pct` derived column per base column:
    total percent change between the first/last available period (or the
    given start_period/end_period), recorded at the end period only — like
    with_cagr, a single number over a range, not a per-period series."""
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
    """Add a `{key}__index` derived column per base column: value rebased so
    base_period = 100 (classic index-number rebasing). Raises ValueError if
    a base column has no value at base_period — rebasing against a missing
    value would silently produce every other period as None too, which is
    worse than failing loudly."""
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
    """Add a `{key}__ma{window}` derived column per base column: the mean of
    the `window` most recent periods with a value (chronological order,
    gaps allowed between them — matches this project's existing convention
    of working over "periods with values", e.g. with_growth)."""
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
    """Add one derived column, `{key_a}__minus_{key_b}` by default: value of
    key_a minus value of key_b for every period both are present. General
    difference-between-series operation — works for two countries, two
    indicators, or a base column and another derived column alike."""
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
    """Add one derived column: numerator_key's value as a percentage of
    denominator_key's value, for every period both are present.

    The precise two-column counterpart to with_share() (which computes a
    share for every OTHER base column against one shared total) — this is
    what a structured {"operation": "share", "numerator_concept": ...,
    "denominator_concept": ...} transformation (core/query_plan.py's
    TransformationSpec, dispatched from core/ask.py) needs: exactly the two
    columns the planner named, nothing else in the table touched."""
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
    """Add one derived column: numerator_key's value divided by
    denominator_key's value, for every period both are present.

    The precise two-column counterpart to with_per_capita() (which divides
    every OTHER base column by one shared population column) — for a
    structured {"operation": "per_capita", "numerator_concept": ...,
    "denominator_concept": ...} transformation."""
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
    """Add one derived column: column_key's value rebased so base_period =
    base_value (default 100, the classic index-number convention).

    The precise single-column counterpart to with_index() (which rebases
    EVERY base column in the table to the same base_period) — for a
    structured {"operation": "index", "input_concept": ...} transformation,
    which targets exactly the one column the planner named. Rebasing every
    base column would be wrong whenever the table also holds an unrelated
    column for a different concept selected by the same question (e.g. a
    population column fetched for a separate per_capita transformation)."""
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
    """Add a `{key}__per_capita` derived column per other base column: that
    column's value divided by the population column's value. A thin,
    explicitly-named wrapper — mathematically the same operation as
    with_ratio(), kept separate because "per capita" is a distinct,
    frequently-requested concept (section 14) worth its own clear label
    rather than requiring a caller to know it's "just a ratio"."""
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
    """Add one derived column: the sum of the listed columns, only for
    periods where every listed column has a value (no silent undercount
    from treating a missing value as zero)."""
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
    """Add one derived column: the unweighted mean of the listed columns,
    only for periods where every listed column has a value."""
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
    """Add one derived column: the weighted mean of the given columns using
    caller-supplied, explicit weights (section 14: "weighted average where
    weights are explicitly defined" — never inferred). Only computed for
    periods where every weighted column has a value, for the same
    no-silent-undercount reason as with_sum()."""
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
    """Add a `{key}__rank` derived column per base column: that column's rank
    among all base columns for each period (1 = highest value). Most
    meaningful when base columns are commensurable, e.g. the same indicator
    across countries — ranking unrelated indicators against each other is
    mechanically well-defined but not necessarily meaningful."""
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
        # Every rank at this period depends on every column that actually
        # took part in the comparison at this period (i.e. had a value) -
        # not the full all_base_keys list, which may include columns with
        # no value at this particular period and so didn't influence the
        # ordering here.
        participating = tuple((k, period) for k, _v in scored)
        for rank, (key, _value) in enumerate(scored, start=1):
            new_values[(period, f"{key}__rank")] = float(rank)
            new_dependencies[(period, f"{key}__rank")] = participating

    return ComparisonTable(columns=tuple(new_columns), values=new_values, cell_dependencies=new_dependencies)

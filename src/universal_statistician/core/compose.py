"""Derived comparison tables built from multiple get_series() calls.

This is what makes the assistant answer "GDP per capita for X, Y, Z from
2015-2024" rather than only "one indicator, one country, one call" — the
capability the MVP scope was explicitly widened for. A ComparisonTable is
plain data (columns + period/column -> value cells); building one is
separated from computing derived columns (growth/ratio/rank) so each step is
independently testable and callers can mix and match.

Every base column keeps the Attribution of the get_series() call it came
from — cells from different countries or indicators must never be blended
without each one's own source staying visible. Derived columns (growth,
ratio, rank) carry no Attribution of their own: they're computed here, not
fetched from any official source, and are labeled `derived=True` so a caller
can never mistake a computed number for one reported directly by a source.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import Attribution, SeriesResult


@dataclass(frozen=True)
class ComparisonColumn:
    key: str
    label: str
    attribution: Attribution | None
    derived: bool = False


@dataclass(frozen=True)
class ComparisonTable:
    columns: tuple[ComparisonColumn, ...]
    #: (period, column_key) -> value
    values: dict[tuple[str, str], float | None]

    def periods(self) -> tuple[str, ...]:
        return tuple(sorted({period for period, _column_key in self.values}))

    def value_at(self, period: str, column_key: str) -> float | None:
        return self.values.get((period, column_key))

    def as_dict(self) -> dict:
        return {
            "columns": [
                {
                    "key": c.key,
                    "label": c.label,
                    "derived": c.derived,
                    "attribution": c.attribution.as_dict() if c.attribution else None,
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
        column = ComparisonColumn(key=ref_area, label=ref_area, attribution=series.attribution)
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
            key=indicator_id, label=indicator_id, attribution=series.attribution
        )
        columns.append((column, series))
    return build_comparison(columns)


def with_growth(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__yoy_growth_pct` derived column per base column: percent
    change from the previous period present in that column (chronological
    order by period string — true for every period format this project uses
    so far: "YYYY" and "YYYY-MM" both sort correctly as plain strings)."""
    base_columns = [c for c in table.columns if not c.derived]
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_columns = list(table.columns)

    for column in base_columns:
        growth_key = f"{column.key}__yoy_growth_pct"
        if growth_key in existing_keys:
            continue

        periods_with_values = sorted(
            p for p in table.periods() if table.value_at(p, column.key) is not None
        )
        new_columns.append(
            ComparisonColumn(
                key=growth_key,
                label=f"{column.label}: YoY growth %",
                attribution=None,
                derived=True,
            )
        )
        for previous, current in zip(periods_with_values, periods_with_values[1:]):
            prev_value = table.value_at(previous, column.key)
            curr_value = table.value_at(current, column.key)
            if prev_value:  # skip growth-from-zero (undefined) and prev_value is None
                new_values[(current, growth_key)] = (curr_value - prev_value) / prev_value * 100

    return ComparisonTable(columns=tuple(new_columns), values=new_values)


def with_ratio(table: ComparisonTable, baseline_key: str) -> ComparisonTable:
    """Add a `{key}__ratio_to_{baseline_key}` derived column per other base
    column: that column's value divided by the baseline column's value, for
    every period both are present."""
    base_columns = [c for c in table.columns if not c.derived]
    if baseline_key not in {c.key for c in base_columns}:
        raise ValueError(f"Unknown baseline column {baseline_key!r}")

    new_values = dict(table.values)
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
            )
        )
        for period in table.periods():
            value = table.value_at(period, column.key)
            baseline_value = table.value_at(period, baseline_key)
            if value is not None and baseline_value:
                new_values[(period, ratio_key)] = value / baseline_value

    return ComparisonTable(columns=tuple(new_columns), values=new_values)


def with_rank(table: ComparisonTable) -> ComparisonTable:
    """Add a `{key}__rank` derived column per base column: that column's rank
    among all base columns for each period (1 = highest value). Most
    meaningful when base columns are commensurable, e.g. the same indicator
    across countries — ranking unrelated indicators against each other is
    mechanically well-defined but not necessarily meaningful."""
    base_columns = [c for c in table.columns if not c.derived]
    existing_keys = {c.key for c in table.columns}
    new_values = dict(table.values)
    new_columns = list(table.columns) + [
        ComparisonColumn(
            key=f"{c.key}__rank", label=f"{c.label}: rank", attribution=None, derived=True
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
        for rank, (key, _value) in enumerate(scored, start=1):
            new_values[(period, f"{key}__rank")] = float(rank)

    return ComparisonTable(columns=tuple(new_columns), values=new_values)

"""answer_question(): the orchestration pipeline behind /ask (Phase 11,
api.py) — question -> interpretation -> query plan -> selection ->
retrieval -> transformations -> validation -> answer -> citations/
provenance -> table + chart, per section 9's target pipeline.

Every step reuses an already-built, already-tested module: planning/ for
interpretation, core/query_plan.py for the plan itself, core/selection.py
for choosing indicators, QueryEngine.get_series for retrieval,
core/compose.py for transformations, core/validation.py for the PASS/
WARNING/FAIL gate, core/provenance.py for citations. This module only adds
the glue: turning a QueryPlan's selected indicators + geographies into a
ComparisonTable, applying plan.transformations by name, and building the
answer text/chart from validated structured data — never from an LLM
rewriting numbers (section 18).
"""

from __future__ import annotations

from universal_statistician.core.answer import AskResult, ChartSeries, ChartSpec
from universal_statistician.core.compose import (
    ComparisonColumn,
    ComparisonTable,
    build_comparison,
    with_absolute_change,
    with_cagr,
    with_cumulative_growth,
    with_growth,
    with_period_over_period_growth,
    with_pp_change,
    with_rank,
)
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import SeriesResult
from universal_statistician.core.provenance import resolve_provenance
from universal_statistician.core.query_plan import QueryPlan, build_query_plan
from universal_statistician.core.selection import select_indicators
from universal_statistician.core.validation import ValidationResult, ValidationStatus, validate_table
from universal_statistician.planning.base import LLMPlanner
from universal_statistician.planning.rule_based_planner import RuleBasedPlanner

#: Transformations applicable with no extra parameters beyond what a
#: QueryPlan already carries (start_period/end_period). Ratio/share/
#: per_capita/difference/index/sum/average/weighted_average all need a
#: caller-specified column key or weights that a bare transformation *name*
#: can't carry — they stay directly callable from compose.py, not dispatched
#: here, an honestly scoped limit of this first /ask implementation (see
#: docs/architecture/nl-platform.md).
_SIMPLE_TRANSFORMATIONS = {
    "growth": with_growth,
    "yoy_growth": with_growth,
    "period_over_period_growth": with_period_over_period_growth,
    "absolute_change": with_absolute_change,
    "pp_change": with_pp_change,
    "percentage_point_change": with_pp_change,
    "rank": with_rank,
}
_RANGE_TRANSFORMATIONS = {
    "cagr": with_cagr,
    "cumulative_growth": with_cumulative_growth,
}


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _fetch_table(engine: QueryEngine, plan: QueryPlan) -> tuple[ComparisonTable | None, list[str]]:
    warnings: list[str] = []
    if not plan.geographies:
        warnings.append(
            "No geography/area was identified for this question; retrieval requires at "
            "least one (see QueryEngine.get_series's ref_area parameter)."
        )
        return None, warnings
    if not plan.selected_indicators:
        warnings.append("No catalog indicator was selected for this question (see QueryPlan.assumptions).")
        return None, warnings

    multiple_indicators = len({c.indicator_id for c in plan.selected_indicators}) > 1
    multiple_geographies = len(plan.geographies) > 1

    columns: list[tuple[ComparisonColumn, SeriesResult]] = []
    for candidate in plan.selected_indicators:
        for ref_area in plan.geographies:
            try:
                series = engine.get_series(
                    candidate.source_id,
                    candidate.indicator_id,
                    ref_area,
                    start_period=plan.start_period,
                    end_period=plan.end_period,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                warnings.append(
                    f"Failed to retrieve {candidate.source_id}/{candidate.indicator_id}/{ref_area}: {exc}"
                )
                continue

            if multiple_indicators and multiple_geographies:
                key, label = f"{candidate.indicator_id}_{ref_area}", f"{candidate.name} ({ref_area})"
            elif multiple_indicators:
                key, label = candidate.indicator_id, candidate.name
            else:
                key, label = ref_area, ref_area

            column = ComparisonColumn(
                key=key,
                label=label,
                attribution=series.attribution,
                unit=series.unit,
                frequency=series.frequency,
                indicator_id=candidate.indicator_id,
                ref_area=ref_area,
            )
            columns.append((column, series))

    if not columns:
        return None, warnings
    return build_comparison(columns), warnings


def _apply_transformations(table: ComparisonTable, plan: QueryPlan) -> tuple[ComparisonTable, list[str]]:
    warnings: list[str] = []
    requested = {_normalize(t) for t in plan.transformations}

    for name in plan.transformations:
        key = _normalize(name)
        if key in _SIMPLE_TRANSFORMATIONS:
            table = _SIMPLE_TRANSFORMATIONS[key](table)
        elif key in _RANGE_TRANSFORMATIONS:
            table = _RANGE_TRANSFORMATIONS[key](
                table, start_period=plan.start_period, end_period=plan.end_period
            )
        elif key in {"", "ranking"}:
            continue
        else:
            warnings.append(
                f"Requested transformation {name!r} is not auto-applied by /ask (it may need "
                "an explicit column reference); the base indicator(s) are still returned."
            )

    if plan.ranking and "rank" not in requested:
        table = with_rank(table)

    return table, warnings


def _unique_sources(table: ComparisonTable) -> tuple[dict, ...]:
    seen: dict[str, dict] = {}
    for c in table.columns:
        if c.attribution and c.attribution.source_id not in seen:
            seen[c.attribution.source_id] = c.attribution.as_dict()
    return tuple(seen.values())


def _provenance_for_latest_period(table: ComparisonTable) -> tuple[dict, ...]:
    results = []
    for c in table.columns:
        periods_with_values = [p for p in table.periods() if table.value_at(p, c.key) is not None]
        if not periods_with_values:
            continue
        latest = periods_with_values[-1]
        try:
            results.append(resolve_provenance(table, c.key, latest).as_dict())
        except ValueError:
            continue  # a base column without attribution; validate_table() already flagged it FAIL
    return tuple(results)


def _build_answer_text(table: ComparisonTable | None, validation: ValidationResult | None) -> str:
    if table is None:
        return "I could not retrieve official data for this question — see warnings for details."

    lines = []
    for column in table.columns:
        periods_with_values = [p for p in table.periods() if table.value_at(p, column.key) is not None]
        if not periods_with_values:
            continue
        latest = periods_with_values[-1]
        value = table.value_at(latest, column.key)
        unit_suffix = f" {column.unit}" if column.unit else ""
        lines.append(f"{column.label} ({latest}): {value}{unit_suffix}")

    if not lines:
        return "No observations were found for the requested indicator(s)/area(s)."

    text = "; ".join(lines) + "."
    if validation is not None and validation.status != ValidationStatus.PASS:
        notable = [f for f in validation.findings if f.status != ValidationStatus.PASS]
        text += " " + " ".join(f"[{f.status.value}] {f.message}" for f in notable)
    return text


def _build_chart_spec(table: ComparisonTable | None, plan: QueryPlan) -> ChartSpec | None:
    if table is None:
        return None
    base_columns = [c for c in table.columns if not c.derived]
    if not base_columns:
        return None

    sources = sorted({c.attribution.source_name for c in base_columns if c.attribution})
    return ChartSpec(
        chart_type="comparison" if len(base_columns) > 1 else "line",
        title=", ".join(plan.concepts) or plan.question,
        x_axis="period",
        y_axis=base_columns[0].unit or "value",
        units=base_columns[0].unit,
        series=tuple(ChartSeries(key=c.key, label=c.label) for c in base_columns),
        source_note="; ".join(sources) if sources else "",
    )


def answer_question(
    engine: QueryEngine, question: str, planner: LLMPlanner | None = None
) -> AskResult:
    planner = planner or RuleBasedPlanner()
    interpretation = planner.interpret(question)
    plan = select_indicators(build_query_plan(question, interpretation, engine))

    if plan.needs_clarification:
        return AskResult(
            question=question,
            query_plan=plan.as_dict(),
            answer=plan.clarification_question or "This question is ambiguous; please clarify.",
            table=None,
            chart=None,
            sources=(),
            provenance=(),
            warnings=("Clarification requested before any retrieval was attempted.",),
            validation=None,
        )

    table, warnings = _fetch_table(engine, plan)
    if table is None:
        return AskResult(
            question=question,
            query_plan=plan.as_dict(),
            answer=_build_answer_text(None, None),
            table=None,
            chart=None,
            sources=(),
            provenance=(),
            warnings=tuple(warnings),
            validation=None,
        )

    table, transform_warnings = _apply_transformations(table, plan)
    warnings.extend(transform_warnings)

    validation = validate_table(
        table,
        requested_geographies=plan.geographies,
        requested_start_period=plan.start_period,
        requested_end_period=plan.end_period,
    )
    chart = _build_chart_spec(table, plan)

    return AskResult(
        question=question,
        query_plan=plan.as_dict(),
        answer=_build_answer_text(table, validation),
        table=table.as_dict(),
        chart=chart.as_dict() if chart else None,
        sources=_unique_sources(table),
        provenance=_provenance_for_latest_period(table),
        warnings=tuple(warnings),
        validation=validation.as_dict(),
    )

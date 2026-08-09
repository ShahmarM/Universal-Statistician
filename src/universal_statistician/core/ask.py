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

import logging
import time

from universal_statistician.core.answer import AskResult, ChartSeries, ChartSpec
from universal_statistician.core.compose import (
    ComparisonColumn,
    ComparisonTable,
    build_comparison,
    with_absolute_change,
    with_cagr,
    with_cumulative_growth,
    with_difference,
    with_growth,
    with_index_column,
    with_per_capita_pair,
    with_period_over_period_growth,
    with_pp_change,
    with_rank,
    with_share_pair,
    with_weighted_average,
)
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import SeriesResult
from universal_statistician.core.provenance import resolve_provenance
from universal_statistician.core.query_plan import QueryPlan, TransformationSpec, build_query_plan
from universal_statistician.core.selection import select_indicators
from universal_statistician.core.validation import ValidationResult, ValidationStatus, validate_table
from universal_statistician.planning.base import LLMPlanner
from universal_statistician.planning.rule_based_planner import RuleBasedPlanner

#: Structured logging (section 26): question received, plan built,
#: indicators selected, transformations applied, validation outcome, total
#: request time — core/engine.py's get_series() separately logs cache
#: hit/miss and per-provider retrieval time, so this module doesn't
#: duplicate that, only the steps specific to this pipeline.
logger = logging.getLogger(__name__)

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


def _elapsed_ms(started_at: float) -> float:
    return round((time.monotonic() - started_at) * 1000, 1)


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
                # Prefer the provider's own unit/semantics (Phase F: some
                # dataflows, e.g. Eurostat's CP_MEUR, know it structurally
                # for every fetch); fall back to the catalog's per-indicator
                # metadata (candidate.unit/.semantics, from
                # engine.search_indicator()) when the provider itself
                # didn't supply one - e.g. World Bank, where unit varies by
                # indicator and only discovery (Phase 2) captures it.
                unit=series.unit or candidate.unit,
                frequency=series.frequency,
                indicator_id=candidate.indicator_id,
                ref_area=ref_area,
                semantics=series.semantics or candidate.semantics,
            )
            columns.append((column, series))

    if not columns:
        return None, warnings
    return build_comparison(columns), warnings


def _column_key_for_concept(
    table: ComparisonTable, plan: QueryPlan, concept: str | None, ref_area: str
) -> tuple[str | None, str | None]:
    """Resolve a transformation's natural-language concept reference to the
    ComparisonTable column holding that concept's values for one geography —
    never an assumed/guessed key. Returns (column_key, error_message); at
    most one is not None."""
    if not concept:
        return None, "transformation is missing a required concept reference"
    candidate = next((c for c in plan.selected_indicators if c.concept == concept), None)
    if candidate is None:
        return None, f"Concept {concept!r} was not resolved to a selected catalog indicator"
    column = next(
        (
            c
            for c in table.columns
            if not c.derived and c.indicator_id == candidate.indicator_id and c.ref_area == ref_area
        ),
        None,
    )
    if column is None:
        return None, (
            f"No data was retrieved for {concept!r} ({candidate.source_id}/"
            f"{candidate.indicator_id}) in {ref_area!r}"
        )
    return column.key, None


def _apply_pair_transformation(
    table: ComparisonTable,
    plan: QueryPlan,
    spec: TransformationSpec,
    *,
    left_concept: str | None,
    right_concept: str | None,
    fn,
    default_name: str,
) -> tuple[ComparisonTable, list[str]]:
    """Shared dispatch for the two-concept structured operations (share,
    per_capita, difference): resolve both concepts to columns per requested
    geography, then apply `fn` (with_share_pair/with_per_capita_pair/
    with_difference) to produce one derived column per geography."""
    warnings: list[str] = []
    for ref_area in plan.geographies:
        left_key, left_err = _column_key_for_concept(table, plan, left_concept, ref_area)
        right_key, right_err = _column_key_for_concept(table, plan, right_concept, ref_area)
        if left_err or right_err:
            warnings.extend(e for e in (left_err, right_err) if e)
            continue
        result_key = f"{ref_area}__{spec.output_name or default_name}"
        try:
            table = fn(table, left_key, right_key, result_key=result_key)
        except ValueError as exc:
            warnings.append(str(exc))
    return table, warnings


def _apply_index(
    table: ComparisonTable, plan: QueryPlan, spec: TransformationSpec
) -> tuple[ComparisonTable, list[str]]:
    warnings: list[str] = []
    if not spec.base_period:
        warnings.append("index transformation requires base_period; base indicator(s) still returned.")
        return table, warnings

    for ref_area in plan.geographies:
        column_key, err = _column_key_for_concept(table, plan, spec.input_concept, ref_area)
        if err:
            warnings.append(err)
            continue
        result_key = f"{ref_area}__{spec.output_name or 'index'}"
        try:
            table = with_index_column(
                table,
                column_key,
                spec.base_period,
                base_value=spec.base_value if spec.base_value is not None else 100.0,
                result_key=result_key,
            )
        except ValueError as exc:
            warnings.append(str(exc))
    return table, warnings


def _apply_weighted_average(
    table: ComparisonTable, plan: QueryPlan, spec: TransformationSpec
) -> tuple[ComparisonTable, list[str]]:
    warnings: list[str] = []
    if not spec.inputs or not spec.weights or len(spec.inputs) != len(spec.weights):
        warnings.append(
            "weighted_average requires `inputs` (geography codes to combine) and `weights` "
            "of equal, non-zero length; base indicator(s) still returned."
        )
        return table, warnings

    weights_by_key: dict[str, float] = {}
    for ref_area, weight in zip(spec.inputs, spec.weights):
        column = next(
            (c for c in table.columns if not c.derived and c.ref_area == ref_area), None
        )
        if column is None:
            warnings.append(f"No retrieved column for area {ref_area!r} in weighted_average inputs")
            continue
        weights_by_key[column.key] = weight

    if len(weights_by_key) < 2:
        warnings.append("weighted_average needs at least two resolved input areas; skipped.")
        return table, warnings

    try:
        table = with_weighted_average(
            table,
            weights_by_key,
            result_key=spec.output_name or "weighted_average",
            result_label=spec.output_name or "Weighted average",
        )
    except ValueError as exc:
        warnings.append(str(exc))
    return table, warnings


def _apply_transformations(table: ComparisonTable, plan: QueryPlan) -> tuple[ComparisonTable, list[str]]:
    warnings: list[str] = []
    requested = {_normalize(t.operation) for t in plan.transformations}

    for spec in plan.transformations:
        key = _normalize(spec.operation)
        if key in _SIMPLE_TRANSFORMATIONS:
            table = _SIMPLE_TRANSFORMATIONS[key](table)
        elif key in _RANGE_TRANSFORMATIONS:
            table = _RANGE_TRANSFORMATIONS[key](
                table, start_period=plan.start_period, end_period=plan.end_period
            )
        elif key == "share":
            table, w = _apply_pair_transformation(
                table, plan, spec,
                left_concept=spec.numerator_concept, right_concept=spec.denominator_concept,
                fn=with_share_pair, default_name="share",
            )
            warnings.extend(w)
        elif key == "per_capita":
            table, w = _apply_pair_transformation(
                table, plan, spec,
                left_concept=spec.numerator_concept, right_concept=spec.denominator_concept,
                fn=with_per_capita_pair, default_name="per_capita",
            )
            warnings.extend(w)
        elif key == "difference":
            table, w = _apply_pair_transformation(
                table, plan, spec,
                left_concept=spec.left_concept, right_concept=spec.right_concept,
                fn=with_difference, default_name="difference",
            )
            warnings.extend(w)
        elif key == "index":
            table, w = _apply_index(table, plan, spec)
            warnings.extend(w)
        elif key == "weighted_average":
            table, w = _apply_weighted_average(table, plan, spec)
            warnings.extend(w)
        elif key in {"", "ranking"}:
            continue
        else:
            warnings.append(
                f"Requested transformation {spec.operation!r} is not auto-applied by /ask (it may "
                "need an explicit column reference); the base indicator(s) are still returned."
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
    started_at = time.monotonic()
    logger.info("ask.question_received", extra={"question": question})

    planner = planner or RuleBasedPlanner()
    interpretation = planner.interpret(question)
    plan = select_indicators(build_query_plan(question, interpretation, engine))
    logger.info(
        "ask.plan_built",
        extra={
            "concepts": list(plan.concepts),
            "candidate_count": len(plan.candidate_indicators),
            "selected": [(c.source_id, c.indicator_id) for c in plan.selected_indicators],
            "needs_clarification": plan.needs_clarification,
        },
    )

    if plan.needs_clarification:
        logger.info(
            "ask.completed",
            extra={"outcome": "needs_clarification", "elapsed_ms": _elapsed_ms(started_at)},
        )
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
        logger.info(
            "ask.completed",
            extra={
                "outcome": "no_table",
                "warning_count": len(warnings),
                "elapsed_ms": _elapsed_ms(started_at),
            },
        )
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
    logger.info(
        "ask.transformations_applied",
        extra={"requested": list(plan.transformations), "warning_count": len(transform_warnings)},
    )

    validation = validate_table(
        table,
        requested_geographies=plan.geographies,
        requested_start_period=plan.start_period,
        requested_end_period=plan.end_period,
    )
    chart = _build_chart_spec(table, plan)
    logger.info(
        "ask.completed",
        extra={
            "outcome": "answered",
            "validation_status": validation.status.value,
            "warning_count": len(warnings),
            "elapsed_ms": _elapsed_ms(started_at),
        },
    )

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

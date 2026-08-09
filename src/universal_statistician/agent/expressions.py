"""Structured calculation-expression schema + executor (Phase 4).

A `CalculationRequest` is the *only* way the LLM can ask for a
computation: a closed `operation` enum plus `result_id` references,
validated here — before `InvestigationState` is touched at all — against
a fixed, per-operation required-field contract. There is no path from an
LLM tool call to arbitrary code, a formula string, or a raw number: every
field either names an operation from `CALCULATE_OPERATIONS` or copies a
`result_id`/period string verbatim from a previous tool result.

`agent/tools.py::calculate()` is a thin wrapper around this module:
parse -> `CalculationRequest.from_dict()` -> `execute_calculation()`. All
dispatch onto `core/compose.py`'s existing `with_*()` functions lives
here, not duplicated in tools.py — this module still doesn't reimplement
a single formula.

Example requests (task section 8):

    {"operation": "cagr", "input": "result_12", "start_period": "2015", "end_period": "2025"}
    {"operation": "share", "numerator": "result_17", "denominator": "result_18"}
    {"operation": "index", "input": "result_4", "base_period": "2015", "base_value": 100}
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from universal_statistician.agent.state import DerivedResult, InvestigationState
from universal_statistician.core.compose import (
    ComparisonTable,
    with_absolute_change,
    with_average,
    with_cagr,
    with_cumulative_growth,
    with_difference,
    with_growth,
    with_index_column,
    with_moving_average,
    with_per_capita_pair,
    with_period_over_period_growth,
    with_pp_change,
    with_rank,
    with_share_pair,
    with_sum,
    with_weighted_average,
)

_SIMPLE_OPS = {
    "growth": with_growth,
    "yoy_growth": with_growth,
    "period_over_period_growth": with_period_over_period_growth,
    "absolute_change": with_absolute_change,
    "pp_change": with_pp_change,
    "percentage_point_change": with_pp_change,
}
_RANGE_OPS = {"cagr": with_cagr, "cumulative_growth": with_cumulative_growth}
_PAIR_OPS = {
    "share": with_share_pair,
    "per_capita": with_per_capita_pair,
    "difference": with_difference,
}
_AGGREGATE_OPS = {"sum": with_sum, "average": with_average}

CALCULATE_OPERATIONS = tuple(
    sorted(
        {
            *_SIMPLE_OPS,
            *_RANGE_OPS,
            *_PAIR_OPS,
            *_AGGREGATE_OPS,
            "index",
            "weighted_average",
            "rank",
            "moving_average",
        }
    )
)


@dataclass(frozen=True)
class CalculationRequest:
    """Validated shape of one `calculate` tool call. Construct only via
    `from_dict()` — the bare constructor performs no validation, so
    callers within this module that already know a request is well-formed
    (none do; `execute_calculation()` always receives a `from_dict()`
    result) aren't tempted to bypass it."""

    operation: str
    input: str | None = None
    inputs: tuple[str, ...] = ()
    numerator: str | None = None
    denominator: str | None = None
    left: str | None = None
    right: str | None = None
    weights: dict[str, float] | None = None
    base_period: str | None = None
    base_value: float = 100.0
    start_period: str | None = None
    end_period: str | None = None
    window: int | None = None
    output_name: str | None = None

    @staticmethod
    def from_dict(payload: dict) -> "CalculationRequest":
        raw_operation = payload.get("operation")
        operation = str(raw_operation or "").strip().lower()
        if operation not in CALCULATE_OPERATIONS:
            raise ValueError(
                f"Unknown operation {raw_operation!r}. Supported operations: "
                f"{list(CALCULATE_OPERATIONS)}."
            )

        weights = payload.get("weights")
        request = CalculationRequest(
            operation=operation,
            input=payload.get("input"),
            inputs=tuple(payload.get("inputs") or ()),
            numerator=payload.get("numerator"),
            denominator=payload.get("denominator"),
            left=payload.get("left"),
            right=payload.get("right"),
            weights=dict(weights) if weights else None,
            base_period=payload.get("base_period"),
            base_value=(
                payload["base_value"] if payload.get("base_value") is not None else 100.0
            ),
            start_period=payload.get("start_period"),
            end_period=payload.get("end_period"),
            window=payload.get("window"),
            output_name=payload.get("output_name"),
        )
        request._check_required_fields()
        return request

    def _check_required_fields(self) -> None:
        """Per-operation required-field contract — raises ValueError with a
        specific, actionable message rather than failing deep inside
        execute_calculation() with a confusing KeyError/TypeError."""
        op = self.operation
        if op in _SIMPLE_OPS or op in _RANGE_OPS:
            if not self.input:
                raise ValueError(f"operation {op!r} requires `input` (a single result_id).")
        elif op == "moving_average":
            if not self.input:
                raise ValueError("operation 'moving_average' requires `input` (a single result_id).")
            if not self.window or self.window < 2:
                raise ValueError("operation 'moving_average' requires `window` (an integer >= 2).")
        elif op == "index":
            if not self.input or not self.base_period:
                raise ValueError("operation 'index' requires `input` and `base_period`.")
        elif op == "difference":
            if not self.left or not self.right:
                raise ValueError("operation 'difference' requires `left` and `right`.")
        elif op in ("share", "per_capita"):
            if not self.numerator or not self.denominator:
                raise ValueError(f"operation {op!r} requires `numerator` and `denominator`.")
        elif op in _AGGREGATE_OPS or op == "rank":
            if len(self.inputs) < 2:
                raise ValueError(f"operation {op!r} requires `inputs` (at least two result_ids).")
        elif op == "weighted_average":
            if not self.weights or len(self.weights) < 2:
                raise ValueError(
                    "operation 'weighted_average' requires `weights` (a "
                    "{result_id: weight} mapping with at least two entries)."
                )
        else:  # pragma: no cover - unreachable, `operation` is already validated
            raise ValueError(f"Unknown operation {op!r}.")


def _rename_column(table: ComparisonTable, old_key: str, new_key: str) -> ComparisonTable:
    new_columns = tuple(replace(c, key=new_key) if c.key == old_key else c for c in table.columns)
    new_values = {
        (period, new_key if key == old_key else key): value
        for (period, key), value in table.values.items()
    }
    new_dependencies = {
        (period, new_key if key == old_key else key): tuple(
            (new_key if k == old_key else k, p) for k, p in deps
        )
        for (period, key), deps in table.cell_dependencies.items()
    }
    return ComparisonTable(columns=new_columns, values=new_values, cell_dependencies=new_dependencies)


def _scratch_table(state: InvestigationState, keys: list[str]) -> ComparisonTable:
    unknown = [k for k in keys if k not in {c.key for c in state.table.columns}]
    if unknown:
        raise ValueError(
            f"Unknown result_id(s) {unknown} — calculate() only accepts result_ids "
            "already produced by retrieve_series/calculate, never an invented value."
        )
    columns = tuple(state.table.column(k) for k in keys)
    values = {(p, k): state.table.value_at(p, k) for k in keys for p in state.table.periods()}
    cell_dependencies = {
        key: deps for key, deps in state.table.cell_dependencies.items() if key[1] in keys
    }
    return ComparisonTable(columns=columns, values=values, cell_dependencies=cell_dependencies)


def _single_new_column_key(before: ComparisonTable, after: ComparisonTable) -> str:
    before_keys = {c.key for c in before.columns}
    new_keys = [c.key for c in after.columns if c.key not in before_keys]
    if not new_keys:
        raise ValueError(
            "This calculation produced no new value — commonly because the input "
            "has fewer observations than the operation needs (e.g. growth needs "
            "at least two periods with values)."
        )
    return new_keys[0]


def execute_calculation(state: InvestigationState, request: CalculationRequest) -> dict:
    """Dispatch a validated CalculationRequest onto core/compose.py. The
    only functions called are the project's existing with_*() calculation
    functions — this never computes a formula itself."""
    op = request.operation
    try:
        if op in _SIMPLE_OPS:
            before = _scratch_table(state, [request.input])
            after = _SIMPLE_OPS[op](before)
            new_key = _single_new_column_key(before, after)
            result_id = state.new_result_id()
            renamed = _rename_column(after, new_key, result_id)
            if request.output_name:
                renamed = replace(
                    renamed,
                    columns=tuple(
                        replace(c, label=request.output_name) if c.key == result_id else c
                        for c in renamed.columns
                    ),
                )
            state.merge_derived_table(renamed, result_id)
            input_ids = (request.input,)

        elif op in _RANGE_OPS:
            before = _scratch_table(state, [request.input])
            after = _RANGE_OPS[op](
                before, start_period=request.start_period, end_period=request.end_period
            )
            new_key = _single_new_column_key(before, after)
            result_id = state.new_result_id()
            renamed = _rename_column(after, new_key, result_id)
            state.merge_derived_table(renamed, result_id)
            input_ids = (request.input,)

        elif op == "moving_average":
            before = _scratch_table(state, [request.input])
            after = with_moving_average(before, request.window)
            new_key = _single_new_column_key(before, after)
            result_id = state.new_result_id()
            renamed = _rename_column(after, new_key, result_id)
            state.merge_derived_table(renamed, result_id)
            input_ids = (request.input,)

        elif op == "index":
            result_id = state.new_result_id()
            table = with_index_column(
                state.table,
                request.input,
                request.base_period,
                base_value=request.base_value,
                result_key=result_id,
            )
            state.merge_derived_table(table, result_id)
            input_ids = (request.input,)

        elif op in _PAIR_OPS:
            a, b = (request.left, request.right) if op == "difference" else (
                request.numerator,
                request.denominator,
            )
            _scratch_table(state, [a, b])  # validates both keys exist
            result_id = state.new_result_id()
            table = _PAIR_OPS[op](state.table, a, b, result_key=result_id)
            state.merge_derived_table(table, result_id)
            input_ids = (a, b)

        elif op in _AGGREGATE_OPS:
            inputs = list(request.inputs)
            _scratch_table(state, inputs)
            result_id = state.new_result_id()
            table = _AGGREGATE_OPS[op](state.table, inputs, result_key=result_id, result_label=op)
            state.merge_derived_table(table, result_id)
            input_ids = tuple(inputs)

        elif op == "weighted_average":
            _scratch_table(state, list(request.weights))
            result_id = state.new_result_id()
            table = with_weighted_average(
                state.table, request.weights, result_key=result_id, result_label="weighted_average"
            )
            state.merge_derived_table(table, result_id)
            input_ids = tuple(request.weights)

        elif op == "rank":
            inputs = list(request.inputs)
            before = _scratch_table(state, inputs)
            after = with_rank(before)
            new_result_ids = []
            for original_key in inputs:
                rank_key = f"{original_key}__rank"
                new_id = state.new_result_id()
                # Renamed fresh from `after` each time (not from a previous
                # rename), and merge_derived_table only reads the one
                # requested key, so no key collisions across iterations.
                renamed = _rename_column(after, rank_key, new_id)
                state.merge_derived_table(renamed, new_id)
                new_result_ids.append({"input": original_key, "result_id": new_id})
            return {
                "operation": op,
                "results": new_result_ids,
                "formula": "descending rank among the given inputs, per period",
            }

        else:  # pragma: no cover - unreachable, `operation` is already validated
            raise ValueError(f"Unknown operation {op!r}.")
    except ValueError as exc:
        return {"operation": op, "error": str(exc)}

    derived_values = {
        period: value
        for period in state.table.periods()
        if (value := state.table.value_at(period, result_id)) is not None
    }
    column = state.table.column(result_id)

    calc_warnings: tuple[str, ...] = ()
    if not derived_values:
        message = (
            f"calculate({op}) on {input_ids} produced no computed values — the "
            "input(s) likely don't have enough overlapping/consecutive periods "
            "for this operation (e.g. growth needs at least two periods, cagr/"
            "cumulative_growth need both a start and end value)."
        )
        state.warnings.append(message)
        calc_warnings = (message,)

    state.derived[result_id] = DerivedResult(
        result_id=result_id,
        operation=op,
        formula=column.formula or "",
        input_result_ids=input_ids,
        warnings=calc_warnings,
    )
    result = {
        "operation": op,
        "result_id": result_id,
        "formula": column.formula,
        "input_result_ids": list(input_ids),
        "values": derived_values,
        "unit": column.unit,
    }
    if calc_warnings:
        result["warning"] = calc_warnings[0]
    return result

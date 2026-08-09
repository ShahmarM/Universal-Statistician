"""Dedicated tests for the structured calculation-expression schema (Phase
4) — separate from tests/test_agent_tools.py's calculate() tests (which
exercise the full execute path against real data): this file is about the
*schema* itself — what gets validated before any calculation runs, and
that malformed/unsupported input is always rejected with a clear message,
never silently coerced or executed as code.
"""

from __future__ import annotations

import pytest

from universal_statistician.agent.expressions import CALCULATE_OPERATIONS, CalculationRequest


def test_from_dict_accepts_a_well_formed_simple_request():
    request = CalculationRequest.from_dict({"operation": "growth", "input": "result_1"})
    assert request.operation == "growth"
    assert request.input == "result_1"


def test_from_dict_normalizes_operation_case_and_whitespace():
    request = CalculationRequest.from_dict({"operation": "  CAGR  ", "input": "result_1"})
    assert request.operation == "cagr"


def test_from_dict_rejects_an_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        CalculationRequest.from_dict({"operation": "eval", "input": "result_1"})


def test_from_dict_rejects_an_empty_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        CalculationRequest.from_dict({"input": "result_1"})


@pytest.mark.parametrize("operation", ["growth", "yoy_growth", "period_over_period_growth", "absolute_change", "pp_change", "cagr", "cumulative_growth"])
def test_single_input_operations_require_input(operation):
    with pytest.raises(ValueError, match="requires `input`"):
        CalculationRequest.from_dict({"operation": operation})


def test_moving_average_requires_input_and_window():
    with pytest.raises(ValueError, match="requires `input`"):
        CalculationRequest.from_dict({"operation": "moving_average", "window": 3})
    with pytest.raises(ValueError, match="requires `window`"):
        CalculationRequest.from_dict({"operation": "moving_average", "input": "result_1"})
    with pytest.raises(ValueError, match="requires `window`"):
        CalculationRequest.from_dict({"operation": "moving_average", "input": "result_1", "window": 1})


def test_index_requires_input_and_base_period():
    with pytest.raises(ValueError, match="requires `input` and `base_period`"):
        CalculationRequest.from_dict({"operation": "index", "input": "result_1"})
    with pytest.raises(ValueError, match="requires `input` and `base_period`"):
        CalculationRequest.from_dict({"operation": "index", "base_period": "2015"})


def test_difference_requires_left_and_right():
    with pytest.raises(ValueError, match="requires `left` and `right`"):
        CalculationRequest.from_dict({"operation": "difference", "left": "result_1"})


@pytest.mark.parametrize("operation", ["share", "per_capita"])
def test_pair_operations_require_numerator_and_denominator(operation):
    with pytest.raises(ValueError, match="requires `numerator` and `denominator`"):
        CalculationRequest.from_dict({"operation": operation, "numerator": "result_1"})


@pytest.mark.parametrize("operation", ["sum", "average", "rank"])
def test_multi_input_operations_require_at_least_two_inputs(operation):
    with pytest.raises(ValueError, match="requires `inputs`"):
        CalculationRequest.from_dict({"operation": operation, "inputs": ["result_1"]})
    with pytest.raises(ValueError, match="requires `inputs`"):
        CalculationRequest.from_dict({"operation": operation})


def test_weighted_average_requires_at_least_two_weights():
    with pytest.raises(ValueError, match="requires `weights`"):
        CalculationRequest.from_dict({"operation": "weighted_average", "weights": {"result_1": 1.0}})
    with pytest.raises(ValueError, match="requires `weights`"):
        CalculationRequest.from_dict({"operation": "weighted_average"})


def test_from_dict_defaults_base_value_to_100():
    request = CalculationRequest.from_dict(
        {"operation": "index", "input": "result_1", "base_period": "2015"}
    )
    assert request.base_value == 100.0


def test_from_dict_preserves_an_explicit_base_value_of_zero():
    # A naive `payload.get("base_value") or 100.0` would wrongly replace an
    # explicit 0 with the default — this is the regression guard for that.
    request = CalculationRequest.from_dict(
        {"operation": "index", "input": "result_1", "base_period": "2015", "base_value": 0}
    )
    assert request.base_value == 0


def test_calculate_operations_is_closed_and_sorted():
    assert list(CALCULATE_OPERATIONS) == sorted(CALCULATE_OPERATIONS)
    assert len(CALCULATE_OPERATIONS) == len(set(CALCULATE_OPERATIONS))
    assert "growth" in CALCULATE_OPERATIONS
    assert "share" in CALCULATE_OPERATIONS
    assert "eval" not in CALCULATE_OPERATIONS
    assert "exec" not in CALCULATE_OPERATIONS


def test_from_dict_ignores_unknown_extra_fields_rather_than_crashing():
    # A model that adds an unexpected field (e.g. a stray "formula" it
    # tried to sneak in) must not crash the whole tool call — from_dict()
    # only reads the fields it knows about.
    request = CalculationRequest.from_dict(
        {"operation": "growth", "input": "result_1", "formula": "result_1 * 2", "code": "os.system('rm -rf /')"}
    )
    assert request.operation == "growth"
    assert not hasattr(request, "formula")
    assert not hasattr(request, "code")

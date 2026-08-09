from __future__ import annotations

from universal_statistician.planning.base import LLMPlanner
from universal_statistician.planning.rule_based_planner import RuleBasedPlanner


def test_rule_based_planner_satisfies_the_llmplanner_protocol():
    assert isinstance(RuleBasedPlanner(), LLMPlanner)


def test_rule_based_planner_uses_the_question_verbatim_as_one_concept():
    interpretation = RuleBasedPlanner().interpret("  GDP per capita in Sweden  ")

    assert interpretation.concepts == ("GDP per capita in Sweden",)
    assert interpretation.geographies == ()
    assert interpretation.needs_clarification is False
    assert interpretation.assumptions  # explains the narrow interpretation, not silent


def test_rule_based_planner_handles_a_blank_question():
    interpretation = RuleBasedPlanner().interpret("   ")

    assert interpretation.concepts == ()

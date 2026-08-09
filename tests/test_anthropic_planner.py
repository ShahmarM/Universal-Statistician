"""AnthropicPlanner tests — same honesty standard as test_chat.py: a fake
client built from real anthropic.types.Message/ToolUseBlock/Usage objects
(ground truth for the response shape), not a hand-guessed dict. No
ANTHROPIC_API_KEY in this sandbox, so a live call genuinely cannot be
exercised here."""

from __future__ import annotations

from anthropic.types import Message, ToolUseBlock, Usage

from universal_statistician.planning.anthropic_planner import (
    PLAN_TOOL_NAME,
    AnthropicPlanner,
)
from universal_statistician.planning.base import LLMPlanner


def _plan_response(input_payload: dict) -> Message:
    return Message(
        id="msg_test",
        model="claude-sonnet-5",
        role="assistant",
        type="message",
        stop_reason="tool_use",
        stop_sequence=None,
        content=[
            ToolUseBlock(type="tool_use", id="tu_1", name=PLAN_TOOL_NAME, input=input_payload)
        ],
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class FakeClient:
    def __init__(self, response: Message) -> None:
        self._response = response
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_anthropic_planner_satisfies_the_llmplanner_protocol():
    assert isinstance(AnthropicPlanner(client=FakeClient(_plan_response({"concepts": []}))), LLMPlanner)


def test_interpret_forces_the_plan_tool_and_parses_its_input():
    client = FakeClient(
        _plan_response(
            {
                "concepts": ["GDP per capita"],
                "geographies": ["AFG", "GEO", "KAZ"],
                "start_period": "2015",
                "end_period": None,
                "transformations": [{"operation": "cumulative_growth"}],
                "comparison": "cross_country",
                "ranking": False,
                "output_type": "comparison_table",
                "assumptions": ["Interpreted 'growth' as real GDP per capita growth."],
                "needs_clarification": False,
                "clarification_question": None,
            }
        )
    )
    planner = AnthropicPlanner(client=client)

    interpretation = planner.interpret(
        "Compare GDP per capita growth in Azerbaijan, Georgia and Kazakhstan since 2015"
    )

    assert interpretation.concepts == ("GDP per capita",)
    assert interpretation.geographies == ("AFG", "GEO", "KAZ")
    assert interpretation.start_period == "2015"
    assert interpretation.comparison == "cross_country"
    assert len(interpretation.transformations) == 1
    assert interpretation.transformations[0].operation == "cumulative_growth"
    assert interpretation.assumptions == ("Interpreted 'growth' as real GDP per capita growth.",)


def test_interpret_parses_a_structured_share_transformation():
    client = FakeClient(
        _plan_response(
            {
                "concepts": [],
                "geographies": ["AZE"],
                "transformations": [
                    {
                        "operation": "share",
                        "numerator_concept": "non-oil GDP",
                        "denominator_concept": "total GDP",
                        "output_name": "non-oil share of GDP",
                    }
                ],
                "output_type": "answer",
                "assumptions": [],
                "needs_clarification": False,
            }
        )
    )
    planner = AnthropicPlanner(client=client)

    interpretation = planner.interpret("What share of Azerbaijan's GDP is non-oil GDP?")

    assert len(interpretation.transformations) == 1
    spec = interpretation.transformations[0]
    assert spec.operation == "share"
    assert spec.numerator_concept == "non-oil GDP"
    assert spec.denominator_concept == "total GDP"
    assert spec.output_name == "non-oil share of GDP"
    # Never a number/code in a transformation - only natural-language concepts.
    assert spec.concepts_referenced() == ("non-oil GDP", "total GDP")

    # tool_choice forces the plan tool — the model cannot answer with free text.
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": PLAN_TOOL_NAME}
    assert call["tools"][0]["name"] == PLAN_TOOL_NAME


def test_interpret_surfaces_needs_clarification():
    client = FakeClient(
        _plan_response(
            {
                "concepts": ["GDP growth"],
                "geographies": ["AFG"],
                "transformations": [],
                "output_type": "answer",
                "assumptions": [],
                "needs_clarification": True,
                "clarification_question": "Do you mean annual or quarterly GDP growth?",
            }
        )
    )
    planner = AnthropicPlanner(client=client)

    interpretation = planner.interpret("Show GDP growth in Afghanistan")

    assert interpretation.needs_clarification is True
    assert interpretation.clarification_question == "Do you mean annual or quarterly GDP growth?"

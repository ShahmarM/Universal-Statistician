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
                "transformations": ["cumulative_growth"],
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
    assert interpretation.transformations == ("cumulative_growth",)
    assert interpretation.assumptions == ("Interpreted 'growth' as real GDP per capita growth.",)

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

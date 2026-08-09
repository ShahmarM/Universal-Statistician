"""Fast / Research / Auto mode selection and orchestration (Phase 5)."""

from __future__ import annotations

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from universal_statistician.agent.llm import AnthropicAgent, AnthropicAnswerWriter
from universal_statistician.agent.modes import (
    answer_question_with_mode,
    run_fast_mode,
    run_research_mode,
    select_mode,
)
from universal_statistician.agent.state import catalog_id
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import QuestionInterpretation

from .helpers import LookupProvider, make_series


class ScriptedPlanner:
    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


def _engine() -> QueryEngine:
    provider = LookupProvider(
        "WB_WDI",
        {("SP_POP_TOTL", "AZE"): make_series("SP_POP_TOTL", "AZE", {"2023": 10.4, "2024": 10.5}, source_id="WB_WDI")},
    )
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="SP_POP_TOTL", source_id="WB_WDI",
                names={"en": "Population, total"}, unit="persons", frequency="A",
            ),
        ]
    )
    return QueryEngine({"WB_WDI": provider}, catalog=catalog)


def _message(*blocks, stop_reason="end_turn") -> Message:
    return Message(
        id="msg_test", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason=stop_reason, stop_sequence=None, content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        return self._responses.pop(0)


# ---- select_mode ------------------------------------------------------


def test_select_mode_respects_an_explicit_override():
    assert select_mode("Population of Azerbaijan", "fast") == "fast"
    assert select_mode("Population of Azerbaijan", "research") == "research"


def test_select_mode_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="Unknown mode"):
        select_mode("q", "turbo")


@pytest.mark.parametrize(
    "question",
    [
        "Population of Azerbaijan in 2024",
        "What was inflation in Georgia in 2023?",
        "Show GDP for France",
    ],
)
def test_select_mode_auto_picks_fast_for_simple_direct_lookups(question):
    assert select_mode(question, "auto") == "fast"


@pytest.mark.parametrize(
    "question",
    [
        "World Bank and IMF give different GDP figures for Azerbaijan. Why?",
        "Compare real GDP growth in Azerbaijan and Georgia",
        "What share of GDP is non-oil GDP?",
        "What drove the increase in unemployment last year?",
        "How much did the non-oil sector contribute to GDP growth?",
    ],
)
def test_select_mode_auto_picks_research_for_investigative_questions(question):
    assert select_mode(question, "auto") == "research"


# ---- run_fast_mode ------------------------------------------------------


def test_run_fast_mode_matches_answer_question():
    engine = _engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    )
    result = run_fast_mode(engine, "Population of Azerbaijan?", planner=planner)
    assert result.table is not None
    assert "10.5" in result.answer


# ---- run_research_mode --------------------------------------------------


def test_run_research_mode_builds_an_answer_from_the_investigation():
    cid = catalog_id("WB_WDI", "SP_POP_TOTL")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Retrieved population.")),
        ]
    )
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)

    result, state = run_research_mode(engine, llm_agent, "Population of Azerbaijan?")

    assert result.table is not None
    assert "10.5" in result.answer
    assert result.validation is not None
    assert state.retrieved


def test_run_research_mode_answers_honestly_when_nothing_was_retrieved():
    client = FakeClient([_message(TextBlock(type="text", text="I could not find a suitable series."))])
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)

    result, state = run_research_mode(engine, llm_agent, "Some question with no data")

    assert result.table is None
    assert "could not retrieve" in result.answer.lower()
    assert result.validation is None


def test_run_research_mode_uses_the_answer_writer_when_it_produces_supported_prose():
    cid = catalog_id("WB_WDI", "SP_POP_TOTL")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Retrieved population.")),
        ]
    )
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)
    writer_client = FakeClient(
        [_message(TextBlock(type="text", text="Azerbaijan's population reached 10.5 million in 2024."))]
    )
    answer_writer = AnthropicAnswerWriter(client=writer_client)

    result, state = run_research_mode(engine, llm_agent, "Population of Azerbaijan?", answer_writer=answer_writer)

    assert result.answer == "Azerbaijan's population reached 10.5 million in 2024."
    assert not any("unsupported" in w.lower() for w in result.warnings)


def test_run_research_mode_falls_back_to_the_deterministic_answer_when_the_writer_fabricates_a_number():
    cid = catalog_id("WB_WDI", "SP_POP_TOTL")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Retrieved population.")),
        ]
    )
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)
    writer_client = FakeClient(
        [
            _message(TextBlock(type="text", text="Azerbaijan's population reached 99.9 million in 2024.")),
            _message(TextBlock(type="text", text="Azerbaijan's population reached 88.8 million in 2024.")),
        ]
    )
    answer_writer = AnthropicAnswerWriter(client=writer_client)

    result, state = run_research_mode(engine, llm_agent, "Population of Azerbaijan?", answer_writer=answer_writer)

    assert "10.5" in result.answer
    assert any("unsupported" in w.lower() for w in result.warnings)


# ---- answer_question_with_mode --------------------------------------------


def test_answer_question_with_mode_routes_fast_questions_to_fast_mode():
    engine = _engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    )
    run = answer_question_with_mode(engine, "Population of Azerbaijan?", mode="auto", planner=planner)
    assert run.mode_used == "fast"
    assert run.investigation is None


def test_answer_question_with_mode_falls_back_to_fast_when_research_has_no_llm():
    engine = _engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    )
    run = answer_question_with_mode(engine, "Compare sources", mode="research", planner=planner)
    assert run.mode_used == "fast"
    assert any("no LLM agent" in w for w in run.result.warnings)


def test_answer_question_with_mode_runs_research_when_an_llm_agent_is_given():
    cid = catalog_id("WB_WDI", "SP_POP_TOTL")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Done.")),
        ]
    )
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)

    run = answer_question_with_mode(
        engine, "Compare data sources for Azerbaijan's population", mode="research", llm_agent=llm_agent
    )

    assert run.mode_used == "research"
    assert run.investigation is not None
    assert run.investigation.retrieved


def test_answer_question_with_mode_passes_the_answer_writer_through_to_research_mode():
    cid = catalog_id("WB_WDI", "SP_POP_TOTL")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Retrieved population.")),
        ]
    )
    engine = _engine()
    llm_agent = AnthropicAgent(client=client)
    writer_client = FakeClient(
        [_message(TextBlock(type="text", text="Azerbaijan's population reached 10.5 million in 2024."))]
    )
    answer_writer = AnthropicAnswerWriter(client=writer_client)

    run = answer_question_with_mode(
        engine,
        "Compare data sources for Azerbaijan's population",
        mode="research",
        llm_agent=llm_agent,
        answer_writer=answer_writer,
    )

    assert run.mode_used == "research"
    assert run.result.answer == "Azerbaijan's population reached 10.5 million in 2024."


def test_answer_question_with_mode_respects_an_explicit_fast_override_even_with_an_llm_agent():
    engine = _engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    )
    llm_agent = AnthropicAgent(client=FakeClient([]))  # would raise if ever called

    run = answer_question_with_mode(
        engine, "Why do sources compare differently?", mode="fast", planner=planner, llm_agent=llm_agent
    )

    assert run.mode_used == "fast"

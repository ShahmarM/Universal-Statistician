"""StatisticalAgent (agent/loop.py, Phase 2) tests.

Same approach test_chat.py already established for this project: a fake
client built from *real* `anthropic.types` objects (ground truth for the
response shape), scripted to return one Message per turn — there is no
ANTHROPIC_API_KEY in this sandbox, so this is the honest ceiling of what's
testable offline. StatisticalAgent's own tool dispatch (agent/tools.py) is
exercised against the same fixture engine test_agent_tools.py uses.
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from universal_statistician.agent.llm import AnthropicAgent
from universal_statistician.agent.loop import AgentLimits, StatisticalAgent
from universal_statistician.agent.state import catalog_id
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import StatisticalSemantics

from .helpers import LookupProvider, make_series


def _engine() -> QueryEngine:
    provider = LookupProvider(
        "WB_WDI",
        {
            ("NY_GDP_MKTP_CD", "AZE"): make_series(
                "NY_GDP_MKTP_CD", "AZE",
                {"2020": 42.6, "2021": 54.6, "2022": 78.8, "2023": 72.4},
                source_id="WB_WDI",
            ),
            ("NY_GDP_MKTP_KD", "AZE"): make_series(
                "NY_GDP_MKTP_KD", "AZE",
                {"2020": 40.0, "2021": 41.0, "2022": 42.0, "2023": 43.0},
                source_id="WB_WDI",
            ),
        },
    )
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_CD", source_id="WB_WDI",
                names={"en": "GDP (current US$)"}, unit="current US$", frequency="A",
                geographic_coverage=("AZE",),
                semantics=StatisticalSemantics(price_basis="nominal"),
            ),
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_KD", source_id="WB_WDI",
                names={"en": "GDP (constant 2015 US$)"}, unit="constant 2015 US$", frequency="A",
                geographic_coverage=("AZE",),
                semantics=StatisticalSemantics(price_basis="real"),
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
    """Returns each scripted Message in order, one per call to .create()."""

    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._responses.pop(0)


class FakeRepeatingClient:
    """Always returns the same tool_use response — a mock LLM that never
    stops calling tools, for the loop-protection test."""

    def __init__(self, block: ToolUseBlock) -> None:
        self.calls: list[dict] = []
        self.messages = self
        self._block = block

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _message(self._block, stop_reason="tool_use")


class RaisingClient:
    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs):
        raise ConnectionError("simulated network failure")


def _agent(client, limits: AgentLimits | None = None) -> StatisticalAgent:
    return StatisticalAgent(_engine(), AnthropicAgent(client=client), limits=limits)


# ---- basic loop mechanics --------------------------------------------------


def test_investigate_stops_immediately_if_the_model_calls_no_tool():
    client = FakeClient([_message(TextBlock(type="text", text="Nothing to investigate."))])
    agent = _agent(client)

    state = agent.investigate("hi")

    assert len(client.calls) == 1
    assert state.iteration_count == 1
    assert state.tool_call_history == []
    assert state.investigator_summary == "Nothing to investigate."


def test_investigate_runs_a_search_then_stops():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "GDP"}),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Found candidates.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("What is GDP?")

    assert len(state.tool_call_history) == 1
    assert state.tool_call_history[0].tool_name == "search_series"
    assert len(state.candidates_considered) == 2  # both WB_WDI GDP entries match


def test_investigate_full_search_inspect_retrieve_validate_sequence():
    cid = catalog_id("WB_WDI", "NY_GDP_MKTP_KD")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "real GDP"}),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_2", name="reject_candidate",
                    input={"catalog_id": catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), "reason": "nominal, not real"},
                ),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_3", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Retrieved real GDP for Azerbaijan.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("Show real GDP growth in Azerbaijan")

    tool_names = [r.tool_name for r in state.tool_call_history]
    assert tool_names == ["search_series", "reject_candidate", "retrieve_series"]
    assert len(state.candidates_rejected) == 1
    assert len(state.retrieved) == 1
    assert state.table.periods()


def test_investigate_handles_multiple_tool_use_blocks_in_one_turn():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "GDP"}),
                ToolUseBlock(
                    type="tool_use", id="tu_2", name="search_series", input={"query": "population"}
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Done.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("q")

    assert len(state.tool_call_history) == 2


def test_investigate_feeds_back_a_tool_error_without_crashing():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="calculate", input={"operation": "growth"}),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="That input was missing, trying something else.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("q")

    assert state.tool_call_history[0].output_summary.get("error")


# ---- limits / stopping rules ----------------------------------------------


def test_investigate_stops_at_max_iterations_without_hanging():
    block = ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "GDP"})
    client = FakeRepeatingClient(block)
    agent = _agent(client, limits=AgentLimits(max_iterations=3, max_tool_calls=100))

    state = agent.investigate("q")

    assert state.iteration_count == 4  # loop breaks once the counter exceeds the limit
    assert len(client.calls) == 3
    assert any("max_iterations" in w for w in state.warnings)


def test_investigate_stops_at_max_tool_calls_without_hanging():
    block = ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "GDP"})
    client = FakeRepeatingClient(block)
    agent = _agent(client, limits=AgentLimits(max_iterations=100, max_tool_calls=3))

    state = agent.investigate("q")

    assert len(state.tool_call_history) == 3
    assert any("max_tool_calls" in w for w in state.warnings)


def test_investigate_caps_retrieve_series_calls_at_max_provider_calls():
    cid = catalog_id("WB_WDI", "NY_GDP_MKTP_CD")
    block = ToolUseBlock(
        type="tool_use", id="tu_1", name="retrieve_series",
        input={"catalog_id": cid, "geographies": ["AZE"]},
    )
    client = FakeRepeatingClient(block)
    agent = _agent(client, limits=AgentLimits(max_iterations=100, max_tool_calls=100, max_provider_calls=2))

    state = agent.investigate("q")

    executed = [r for r in state.tool_call_history if "error" not in r.output_summary]
    assert len(executed) == 2


def test_investigate_stops_cleanly_when_the_llm_call_fails():
    agent = _agent(RaisingClient())

    state = agent.investigate("q")

    assert state.tool_call_history == []
    assert any("LLM call failed" in w for w in state.warnings)


# ---- Phase 3: LLM candidate inspection replaces blind Top-1 selection -----
#
# core/selection.py::select_indicators() (blind, automatic Top-1) is never
# called anywhere in this file — the agent path has no access to it at all
# (agent/tools.py doesn't import selection.py). These tests prove the
# *replacement* mechanism works: inspecting multiple candidates before
# choosing, and searching again after rejecting an unsuitable one, rather
# than only asserting the negative ("selection.py wasn't called").


def test_investigate_inspects_both_gdp_candidates_before_choosing_the_real_one():
    # Task section 4's own example: a bare "GDP" search must not result in
    # picking current-price GDP just because it scores highly — the agent
    # inspects the actual candidates' price_basis metadata first.
    nominal = catalog_id("WB_WDI", "NY_GDP_MKTP_CD")
    real = catalog_id("WB_WDI", "NY_GDP_MKTP_KD")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="search_series", input={"query": "GDP growth"}),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(type="tool_use", id="tu_2", name="inspect_series", input={"catalog_id": nominal}),
                ToolUseBlock(type="tool_use", id="tu_3", name="inspect_series", input={"catalog_id": real}),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_4", name="reject_candidate",
                    input={"catalog_id": nominal, "reason": "current-price (nominal), question asks for growth in real terms"},
                ),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_5", name="retrieve_series",
                    input={"catalog_id": real, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Used the constant-price series.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("Show GDP growth in Azerbaijan")

    inspected = [r.input["catalog_id"] for r in state.tool_call_history if r.tool_name == "inspect_series"]
    assert set(inspected) == {nominal, real}
    assert state.candidates_rejected[0].catalog_id == nominal
    assert list(state.retrieved.values())[0].catalog_id == real


def test_investigate_searches_again_after_rejecting_an_incompatible_candidate():
    # task section 19's "Agent retry": the first candidate found is
    # unsuitable, so the agent rejects it and issues a *second* search
    # rather than settling for what it already has.
    only_candidate = catalog_id("WB_WDI", "NY_GDP_MKTP_CD")
    real = catalog_id("WB_WDI", "NY_GDP_MKTP_KD")
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="search_series",
                    input={"query": "GDP", "source_preference": "WB_WDI", "limit": 1},
                ),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_2", name="reject_candidate",
                    input={"catalog_id": only_candidate, "reason": "nominal, need real GDP"},
                ),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(type="tool_use", id="tu_3", name="search_series", input={"query": "real GDP constant prices"}),
                stop_reason="tool_use",
            ),
            _message(
                ToolUseBlock(
                    type="tool_use", id="tu_4", name="retrieve_series",
                    input={"catalog_id": real, "geographies": ["AZE"]},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Found and used the real GDP series on the second search.")),
        ]
    )
    agent = _agent(client)

    state = agent.investigate("Real GDP growth in Azerbaijan")

    search_calls = [r for r in state.tool_call_history if r.tool_name == "search_series"]
    assert len(search_calls) == 2
    assert real in state.resolve_result_ids()["retrieved"].values()


# ---- evidence package -----------------------------------------------------


def test_evidence_package_reflects_the_investigation():
    cid = catalog_id("WB_WDI", "NY_GDP_MKTP_CD")
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
    agent = _agent(client)

    state = agent.investigate("What is Azerbaijan's GDP?")
    package = state.evidence_package()

    assert package["question"] == "What is Azerbaijan's GDP?"
    assert package["table"]["rows"]
    assert "investigator_summary" not in package  # debug-only, never in the evidence package

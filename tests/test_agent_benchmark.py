"""Agent Phase 9: offline regression counterpart to
scripts/benchmark_agent_vs_legacy.py (which runs all 50 questions across 10
countries and isn't part of the regular suite -- see that script's module
docstring for why both modes are driven by scripted doubles rather than a
live model, and docs/benchmarks/agent-vs-legacy-mode.md for the full
results).

This file hardcodes a small two-country fixture reproducing the same five
capability categories the benchmark measures, and asserts the *exact*
claim each category exists to demonstrate -- the concrete pattern
tests/test_search_quality.py already established for scripts/
benchmark_catalog_search.py's live-catalog findings. A future change to
core/selection.py or agent/loop.py that silently erodes one of these
capability differences fails a red test here, not just a lower number in a
report nobody re-runs.

Also covers task section 19's explicit "Definition of Done" scenarios:
real-GDP-growth comparison after rejecting the wrong candidate, explaining
why two sources' GDP figures differ, and computing a share from two
independently-retrieved series -- all exercised through the real
StatisticalAgent loop (agent/loop.py), trusted tools (agent/tools.py), and
expression executor (agent/expressions.py), with a scripted LLM client
built from real anthropic.types objects (same technique as
tests/test_agent_loop.py).
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from universal_statistician.agent.llm import AnthropicAgent
from universal_statistician.agent.loop import AgentLimits
from universal_statistician.agent.modes import run_research_mode
from universal_statistician.agent.state import catalog_id
from universal_statistician.core.ask import answer_question
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import StatisticalSemantics
from universal_statistician.core.query_plan import QuestionInterpretation

from .helpers import LookupProvider, make_series

_WB = "WB_WDI"
_IMF = "IMF_DATA"
NOMINAL_GDP_ID = "NY_GDP_MKTP_CD"
REAL_GDP_ID = "NY_GDP_MKTP_KD"
POP_ID = "SP_POP_TOTL"
GOV_EXP_ID = "GC_XPN_TOTL_CD"
UNEMP_UNADJ_ID = "SL_UEM_TOTL_ZS"
UNEMP_SA_ID = "SL_UEM_TOTL_SA_ZS"
IMF_GDP_ID = "NGDP"

COUNTRIES = ("AZE", "GEO")


def _engine() -> QueryEngine:
    wb_table, imf_table = {}, {}
    for i, country in enumerate(COUNTRIES):
        wb_table[(NOMINAL_GDP_ID, country)] = make_series(
            NOMINAL_GDP_ID, country, {"2021": 40.0 + i, "2022": 46.0 + i, "2023": 52.0 + i}, source_id=_WB
        )
        wb_table[(REAL_GDP_ID, country)] = make_series(
            REAL_GDP_ID, country, {"2021": 38.0 + i, "2022": 39.5 + i, "2023": 41.0 + i}, source_id=_WB
        )
        wb_table[(POP_ID, country)] = make_series(POP_ID, country, {"2023": 10.0 + i}, source_id=_WB)
        wb_table[(GOV_EXP_ID, country)] = make_series(GOV_EXP_ID, country, {"2023": 8.0 + i}, source_id=_WB)
        wb_table[(UNEMP_UNADJ_ID, country)] = make_series(UNEMP_UNADJ_ID, country, {"2023": 6.0 + i}, source_id=_WB)
        wb_table[(UNEMP_SA_ID, country)] = make_series(UNEMP_SA_ID, country, {"2023": 5.5 + i}, source_id=_WB)
        imf_table[(IMF_GDP_ID, country)] = make_series(
            IMF_GDP_ID, country, {"2021": 41.5 + i, "2022": 44.0 + i, "2023": 49.5 + i}, source_id=_IMF
        )

    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id=POP_ID, source_id=_WB, names={"en": "Population, total"},
                unit="persons", frequency="A", geographic_coverage=COUNTRIES,
            ),
            IndicatorEntry(
                indicator_id=NOMINAL_GDP_ID, source_id=_WB, names={"en": "GDP (current US$)"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="nominal"),
            ),
            IndicatorEntry(
                indicator_id=REAL_GDP_ID, source_id=_WB, names={"en": "GDP (constant 2015 US$)"},
                unit="constant 2015 US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="real"),
            ),
            IndicatorEntry(
                indicator_id=GOV_EXP_ID, source_id=_WB,
                names={"en": "General government final consumption expenditure (current US$)"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
            ),
            IndicatorEntry(
                indicator_id=UNEMP_UNADJ_ID, source_id=_WB,
                names={"en": "Unemployment, total (% of total labor force)"},
                unit="% of labor force", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(seasonally_adjusted=False),
            ),
            IndicatorEntry(
                indicator_id=UNEMP_SA_ID, source_id=_WB,
                names={"en": "Unemployment, total, seasonally adjusted (% of total labor force)"},
                unit="% of labor force", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(seasonally_adjusted=True),
            ),
            IndicatorEntry(
                indicator_id=IMF_GDP_ID, source_id=_IMF,
                names={"en": "Gross Domestic Product, Current Prices"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="nominal"),
            ),
        ]
    )
    return QueryEngine({_WB: LookupProvider(_WB, wb_table), _IMF: LookupProvider(_IMF, imf_table)}, catalog=catalog)


class _ScriptedPlanner:
    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


def _msg(*blocks, stop_reason="end_turn") -> Message:
    return Message(
        id="msg_bench", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason=stop_reason, stop_sequence=None, content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _tool_use(tool_id, name, tool_input) -> Message:
    return _msg(ToolUseBlock(type="tool_use", id=tool_id, name=name, input=tool_input), stop_reason="tool_use")


def _text(text) -> Message:
    return _msg(TextBlock(type="text", text=text))


class _ScriptedClient:
    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        return self._responses.pop(0)


def _base_indicator_ids(table) -> set[str]:
    if not table:
        return set()
    return {c["indicator_id"] for c in table["columns"] if not c["derived"] and c.get("indicator_id")}


def _base_source_ids(table) -> set[str]:
    if not table:
        return set()
    return {c["attribution"]["source_id"] for c in table["columns"] if not c["derived"] and c.get("attribution")}


# ---- category 1: simple lookup -- parity expected --------------------------


def test_simple_lookup_both_modes_retrieve_the_right_indicator():
    engine = _engine()
    interp = QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    fast = answer_question(engine, "What was AZE's population in 2023?", planner=_ScriptedPlanner(interp))

    cid = catalog_id(_WB, POP_ID)
    llm_agent = AnthropicAgent(
        client=_ScriptedClient(
            [
                _tool_use("t1", "search_series", {"query": "population", "geography": "AZE"}),
                _tool_use("t2", "retrieve_series", {"catalog_id": cid, "geographies": ["AZE"]}),
                _text("Retrieved population."),
            ]
        )
    )
    research, _state = run_research_mode(engine, llm_agent, "What was AZE's population in 2023?")

    assert POP_ID in _base_indicator_ids(fast.table)
    assert POP_ID in _base_indicator_ids(research.table)


# ---- category 2: price-basis ambiguity (real vs nominal GDP) ---------------


def test_fast_mode_blindly_picks_nominal_gdp_when_asked_for_real_gdp_growth():
    # Reproduces the exact failure mode task section 5's worked example
    # describes: a naive planner reduces "real GDP growth" to the bare
    # concept "GDP" (price_basis isn't a QuestionInterpretation field at
    # all), and select_indicators() has no basis to prefer the real-price
    # candidate over the nominal one, so the better-ranked candidate wins
    # regardless of which one the question actually needs.
    engine = _engine()
    interp = QuestionInterpretation(concepts=("GDP",), geographies=("AZE",), output_type="answer")
    fast = answer_question(engine, "What was AZE's real GDP growth?", planner=_ScriptedPlanner(interp))

    assert fast.table is not None
    assert _base_indicator_ids(fast.table) == {NOMINAL_GDP_ID}
    assert REAL_GDP_ID not in _base_indicator_ids(fast.table)


def test_research_mode_inspects_and_rejects_nominal_gdp_then_retrieves_and_computes_real_growth():
    # Task section 5's Definition of Done, worked end to end: search ->
    # inspect a wrong candidate -> reject it with a recorded reason ->
    # inspect the right one -> retrieve -> calculate growth -> validate.
    engine = _engine()
    nominal_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    real_cid = catalog_id(_WB, REAL_GDP_ID)
    llm_agent = AnthropicAgent(
        client=_ScriptedClient(
            [
                _tool_use("t1", "search_series", {"query": "real GDP growth", "geography": "AZE"}),
                _tool_use("t2", "inspect_series", {"catalog_id": nominal_cid}),
                _tool_use("t3", "reject_candidate", {"catalog_id": nominal_cid, "reason": "nominal, not real GDP"}),
                _tool_use("t4", "inspect_series", {"catalog_id": real_cid}),
                _tool_use("t5", "retrieve_series", {"catalog_id": real_cid, "geographies": ["AZE"]}),
                _tool_use("t6", "calculate", {"operation": "growth", "input": "result_1"}),
                _tool_use("t7", "validate", {"result_ids": ["result_2"]}),
                _text("Computed real GDP growth for AZE from the constant-price series."),
            ]
        )
    )

    research, state = run_research_mode(engine, llm_agent, "What was AZE's real GDP growth?")

    assert _base_indicator_ids(research.table) == {REAL_GDP_ID}
    assert len(state.candidates_rejected) == 1
    assert state.candidates_rejected[0].catalog_id == nominal_cid
    assert any(c["derived"] for c in research.table["columns"])
    assert research.validation is not None
    assert research.validation["status"] in ("PASS", "WARNING")


# ---- category 3: multi-source discrepancy explanation -----------------------


def test_fast_mode_can_only_ever_surface_one_source_for_a_discrepancy_question():
    # QuestionInterpretation/QueryPlan have no "compare across sources"
    # concept at all -- one concept resolves to exactly one selected
    # indicator (core/selection.py's docstring), so a "why do two sources
    # differ" question structurally cannot be answered by fast mode, no
    # matter how good the natural-language interpretation is.
    engine = _engine()
    interp = QuestionInterpretation(concepts=("GDP",), geographies=("AZE",), output_type="answer")
    fast = answer_question(engine, "Why do World Bank and IMF GDP figures for AZE differ?", planner=_ScriptedPlanner(interp))

    assert len(fast.sources) <= 1


def test_research_mode_retrieves_both_sources_and_compares_them():
    # Task section 5's second required capability: explaining a WB-vs-IMF
    # discrepancy by actually investigating both sources, not returning one.
    engine = _engine()
    wb_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    imf_cid = catalog_id(_IMF, IMF_GDP_ID)
    llm_agent = AnthropicAgent(
        client=_ScriptedClient(
            [
                _tool_use("t1", "search_series", {"query": "GDP", "geography": "AZE", "source_preference": _WB}),
                _tool_use("t2", "retrieve_series", {"catalog_id": wb_cid, "geographies": ["AZE"]}),
                _tool_use("t3", "search_series", {"query": "GDP", "geography": "AZE", "source_preference": _IMF}),
                _tool_use("t4", "retrieve_series", {"catalog_id": imf_cid, "geographies": ["AZE"]}),
                _tool_use("t5", "compare_series", {"result_ids": ["result_1", "result_2"]}),
                _tool_use("t6", "validate", {"result_ids": ["result_1", "result_2"]}),
                _text("Compared WB and IMF GDP figures for AZE; they differ, likely due to vintage/methodology."),
            ]
        )
    )

    research, state = run_research_mode(engine, llm_agent, "Why do World Bank and IMF GDP figures for AZE differ?")

    assert _base_source_ids(research.table) == {_WB, _IMF}
    compare_calls = [r for r in state.tool_call_history if r.tool_name == "compare_series"]
    assert len(compare_calls) == 1


# ---- category 4: independent multi-series share -----------------------------


def test_fast_mode_cannot_compute_a_share_from_a_freeform_question():
    # No `transformations` are extracted by a naive planner echoing the
    # question text -- there's no structured way to notice "share of"
    # implies share(numerator, denominator) over two different concepts.
    engine = _engine()
    interp = QuestionInterpretation(
        concepts=("government expenditure share of GDP",), geographies=("AZE",), output_type="answer"
    )
    fast = answer_question(engine, "What share of AZE's GDP is government expenditure?", planner=_ScriptedPlanner(interp))

    assert fast.table is None


def test_research_mode_retrieves_both_series_independently_and_computes_the_share():
    # Task section 5's third required capability: numerator and denominator
    # found independently, then a deterministic ratio calculation -- never
    # an LLM-supplied number.
    engine = _engine()
    govexp_cid = catalog_id(_WB, GOV_EXP_ID)
    gdp_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    llm_agent = AnthropicAgent(
        client=_ScriptedClient(
            [
                _tool_use("t1", "search_series", {"query": "government expenditure", "geography": "AZE"}),
                _tool_use("t2", "retrieve_series", {"catalog_id": govexp_cid, "geographies": ["AZE"]}),
                _tool_use("t3", "search_series", {"query": "GDP", "geography": "AZE"}),
                _tool_use("t4", "retrieve_series", {"catalog_id": gdp_cid, "geographies": ["AZE"]}),
                _tool_use("t5", "calculate", {"operation": "share", "numerator": "result_1", "denominator": "result_2"}),
                _tool_use("t6", "validate", {"result_ids": ["result_3"]}),
                _text("Computed government expenditure as a share of GDP for AZE."),
            ]
        )
    )

    research, state = run_research_mode(engine, llm_agent, "What share of AZE's GDP is government expenditure?")

    assert "result_3" in state.derived
    assert state.derived["result_3"].operation == "share"
    # 8.0 / 52.0 * 100 (AZE's fixture values, both observed in 2023) --
    # computed by core/compose.py's existing with_share_pair() as a
    # percentage, never supplied by the scripted "LLM".
    row_2023 = next(row for row in research.table["rows"] if row["period"] == "2023")
    assert abs(row_2023["result_3"] - 8.0 / 52.0 * 100) < 1e-9


# ---- category 5: seasonal-adjustment ambiguity ------------------------------


def test_fast_mode_blindly_picks_an_unemployment_variant_when_seasonal_adjustment_matters():
    engine = _engine()
    interp = QuestionInterpretation(concepts=("unemployment",), geographies=("AZE",), output_type="answer")
    fast = answer_question(engine, "What is AZE's seasonally adjusted unemployment rate?", planner=_ScriptedPlanner(interp))

    assert fast.table is not None
    assert UNEMP_SA_ID not in _base_indicator_ids(fast.table)


def test_research_mode_finds_the_seasonally_adjusted_series_specifically():
    engine = _engine()
    unadj_cid = catalog_id(_WB, UNEMP_UNADJ_ID)
    adj_cid = catalog_id(_WB, UNEMP_SA_ID)
    llm_agent = AnthropicAgent(
        client=_ScriptedClient(
            [
                _tool_use("t1", "search_series", {"query": "seasonally adjusted unemployment rate", "geography": "AZE"}),
                _tool_use("t2", "inspect_series", {"catalog_id": unadj_cid}),
                _tool_use("t3", "reject_candidate", {"catalog_id": unadj_cid, "reason": "not seasonally adjusted"}),
                _tool_use("t4", "inspect_series", {"catalog_id": adj_cid}),
                _tool_use("t5", "retrieve_series", {"catalog_id": adj_cid, "geographies": ["AZE"]}),
                _tool_use("t6", "validate", {"result_ids": ["result_1"]}),
                _text("Retrieved the seasonally adjusted unemployment rate for AZE."),
            ]
        )
    )

    research, _state = run_research_mode(engine, llm_agent, "What is AZE's seasonally adjusted unemployment rate?")

    assert _base_indicator_ids(research.table) == {UNEMP_SA_ID}


# ---- loop-protection sanity (task section 6) --------------------------------


def test_a_bounded_investigation_terminates_even_against_a_never_stopping_client():
    # Not a new claim (tests/test_agent_loop.py already proves termination
    # in isolation) -- reproduced here as a regression guard specifically
    # through run_research_mode()'s orchestration layer, since that's what
    # a real /ask request actually calls.
    engine = _engine()
    cid = catalog_id(_WB, POP_ID)
    responses = [
        _tool_use(f"t{i}", "search_series", {"query": "population", "geography": "AZE"}) for i in range(40)
    ]
    llm_agent = AnthropicAgent(client=_ScriptedClient(responses))

    research, state = run_research_mode(
        engine, llm_agent, "Population of AZE?", limits=AgentLimits(max_tool_calls=5, max_iterations=5)
    )

    assert state.iteration_count <= 5
    assert any("max_" in w for w in state.warnings)

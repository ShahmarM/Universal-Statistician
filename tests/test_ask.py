from __future__ import annotations

from universal_statistician.core.ask import answer_question
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import QuestionInterpretation
from universal_statistician.core.validation import ValidationStatus

from .helpers import LookupProvider, make_series


class ScriptedPlanner:
    """Fake LLMPlanner returning a pre-scripted QuestionInterpretation —
    same pattern as test_chat.py's FakeClient: inject a known result rather
    than depending on a real model call."""

    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


def _engine_with_population() -> QueryEngine:
    provider = LookupProvider(
        "WB_WDI",
        {
            ("SP_POP_TOTL", "AFG"): make_series(
                "SP_POP_TOTL", "AFG", {"2019": 10.0, "2020": 11.0}, source_id="WB_WDI"
            ),
            ("SP_POP_TOTL", "USA"): make_series(
                "SP_POP_TOTL", "USA", {"2019": 300.0, "2020": 310.0}, source_id="WB_WDI"
            ),
        },
    )
    catalog = Catalog()
    catalog.add(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    return QueryEngine({"WB_WDI": provider}, catalog=catalog)


def test_answer_question_returns_a_clarification_without_retrieving_anything():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("GDP growth",),
            needs_clarification=True,
            clarification_question="Do you mean real or nominal GDP growth?",
        )
    )

    result = answer_question(engine, "GDP growth?", planner=planner)

    assert result.answer == "Do you mean real or nominal GDP growth?"
    assert result.table is None
    assert result.validation is None
    assert "Clarification requested" in result.warnings[0]


def test_answer_question_reports_missing_geography_without_crashing():
    engine = _engine_with_population()
    planner = ScriptedPlanner(QuestionInterpretation(concepts=("population",)))

    result = answer_question(engine, "population", planner=planner)

    assert result.table is None
    assert any("No geography" in w for w in result.warnings)
    assert "could not retrieve" in result.answer


def test_answer_question_builds_a_full_result_for_a_single_indicator_single_area():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AFG",))
    )

    result = answer_question(engine, "population of Afghanistan", planner=planner)

    assert result.table is not None
    assert result.table["columns"][0]["key"] == "AFG"
    # single indicator -> column labeled by area (matches compare_across_countries'
    # existing convention), not by indicator name
    assert "AFG (2020): 11.0" in result.answer
    assert result.validation["status"] == "PASS"
    assert result.sources[0]["source_id"] == "WB_WDI"
    assert result.provenance  # at least one resolved provenance node
    assert result.chart["chart_type"] == "line"
    assert result.chart["series"] == [{"key": "AFG", "label": "AFG"}]


def test_answer_question_handles_cross_country_comparison():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("population",), geographies=("AFG", "USA"), comparison="cross_country"
        )
    )

    result = answer_question(engine, "compare population", planner=planner)

    assert result.table is not None
    keys = {c["key"] for c in result.table["columns"]}
    assert keys == {"AFG", "USA"}
    assert result.chart["chart_type"] == "comparison"
    assert len(result.sources) == 1  # same source for both


def test_answer_question_applies_a_named_transformation():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("population",), geographies=("AFG",), transformations=("growth",)
        )
    )

    result = answer_question(engine, "population growth in Afghanistan", planner=planner)

    keys = {c["key"] for c in result.table["columns"]}
    assert "AFG__yoy_growth_pct" in keys


def test_answer_question_notes_an_unsupported_transformation_without_failing():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("population",), geographies=("AFG",), transformations=("some_unknown_op",)
        )
    )

    result = answer_question(engine, "population", planner=planner)

    assert result.table is not None  # base retrieval still succeeds
    assert any("not auto-applied" in w for w in result.warnings)


def test_answer_question_surfaces_validation_warnings_in_the_answer_text():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AFG", "DEU"))
    )

    result = answer_question(engine, "population of AFG and DEU", planner=planner)

    # DEU has no provider entry -> get_series fails -> retrieval warning
    assert any("Failed to retrieve" in w for w in result.warnings)
    # only AFG actually made it into the table
    assert {c["key"] for c in result.table["columns"]} == {"AFG"}


def test_answer_question_reports_ranking_when_plan_asks_for_it():
    engine = _engine_with_population()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("population",), geographies=("AFG", "USA"), ranking=True
        )
    )

    result = answer_question(engine, "rank by population", planner=planner)

    keys = {c["key"] for c in result.table["columns"]}
    assert "AFG__rank" in keys and "USA__rank" in keys

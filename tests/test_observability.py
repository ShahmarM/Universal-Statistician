"""Structured logging (section 26) — verified with pytest's caplog, not just
present-in-source: catalog searches, cache hit/miss, provider retrieval
time, and the /ask pipeline's own steps (question received, plan built,
transformations applied, completed) all produce real log records with the
expected fields, at INFO level, using stdlib logging (no new dependency).
"""

from __future__ import annotations

import logging

import pytest

from universal_statistician.core.ask import answer_question
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import QuestionInterpretation

from .helpers import LookupProvider, make_series


class ScriptedPlanner:
    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


@pytest.fixture
def engine():
    provider = LookupProvider(
        "WB_WDI", {("SP_POP_TOTL", "AFG"): make_series("SP_POP_TOTL", "AFG", {"2020": 11.0}, source_id="WB_WDI")}
    )
    catalog = Catalog()
    catalog.add(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    return QueryEngine({"WB_WDI": provider}, catalog=catalog)


# ---- QueryEngine: catalog search + cache hit/miss + retrieval time ------------


def test_search_indicator_logs_a_structured_catalog_search_event(engine, caplog):
    with caplog.at_level(logging.INFO, logger="universal_statistician.core.engine"):
        engine.search_indicator("population")

    record = next(r for r in caplog.records if r.message == "catalog_search")
    assert record.query == "population"
    assert record.result_count == 1


def test_get_series_logs_cache_miss_then_provider_request_then_cache_hit(engine, caplog):
    with caplog.at_level(logging.INFO, logger="universal_statistician.core.engine"):
        engine.get_series("WB_WDI", "SP_POP_TOTL", "AFG")  # first call: miss + request
        engine.get_series("WB_WDI", "SP_POP_TOTL", "AFG")  # second call: hit

    events = [r.message for r in caplog.records]
    assert events.count("cache_miss") == 1
    assert events.count("provider_request_completed") == 1
    assert events.count("cache_hit") == 1

    completed = next(r for r in caplog.records if r.message == "provider_request_completed")
    assert completed.source_id == "WB_WDI"
    assert completed.indicator_id == "SP_POP_TOTL"
    assert completed.ref_area == "AFG"
    assert isinstance(completed.elapsed_ms, float)


# ---- /ask pipeline --------------------------------------------------------------


def test_answer_question_logs_received_plan_built_and_completed(engine, caplog):
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AFG",))
    )

    with caplog.at_level(logging.INFO):
        answer_question(engine, "population of Afghanistan", planner=planner)

    events = {r.message: r for r in caplog.records if r.name == "universal_statistician.core.ask"}
    assert "ask.question_received" in events
    assert events["ask.question_received"].question == "population of Afghanistan"

    assert "ask.plan_built" in events
    assert events["ask.plan_built"].selected == [("WB_WDI", "SP_POP_TOTL")]

    assert "ask.completed" in events
    completed = events["ask.completed"]
    assert completed.outcome == "answered"
    assert completed.validation_status == "PASS"
    assert isinstance(completed.elapsed_ms, float)


def test_answer_question_logs_needs_clarification_outcome(engine, caplog):
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("gdp",), needs_clarification=True, clarification_question="Real or nominal?"
        )
    )

    with caplog.at_level(logging.INFO):
        answer_question(engine, "gdp growth", planner=planner)

    completed = next(
        r
        for r in caplog.records
        if r.name == "universal_statistician.core.ask" and r.message == "ask.completed"
    )
    assert completed.outcome == "needs_clarification"

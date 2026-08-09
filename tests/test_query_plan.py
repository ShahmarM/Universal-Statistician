from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import (
    CandidateIndicator,
    QuestionInterpretation,
    QueryPlan,
    build_query_plan,
)

from .helpers import LookupProvider


def _engine_with_catalog() -> QueryEngine:
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="NY_GDP_PCAP_CD", source_id="WB_WDI", names={"en": "GDP per capita"}
            ),
            IndicatorEntry(
                indicator_id="SL_UEM_TOTL", source_id="WB_WDI", names={"en": "Unemployment rate"}
            ),
        ]
    )
    return QueryEngine({"WB_WDI": LookupProvider("WB_WDI", {})}, catalog=catalog)


def test_build_query_plan_resolves_concepts_via_real_catalog_search():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(
        concepts=("GDP per capita",),
        geographies=("AFG", "USA"),
        comparison="cross_country",
    )

    plan = build_query_plan("compare GDP per capita", interpretation, engine)

    assert plan.question == "compare GDP per capita"
    assert plan.concepts == ("GDP per capita",)
    assert plan.geographies == ("AFG", "USA")
    assert plan.comparison == "cross_country"
    assert [c.indicator_id for c in plan.candidate_indicators] == ["NY_GDP_PCAP_CD"]
    assert plan.candidate_indicators[0].concept == "GDP per capita"
    assert plan.candidate_indicators[0].source_id == "WB_WDI"


def test_build_query_plan_resolves_multiple_concepts_independently():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(
        concepts=("GDP per capita", "unemployment"), comparison="cross_indicator"
    )

    plan = build_query_plan("gdp vs unemployment", interpretation, engine)

    concepts_seen = {c.concept for c in plan.candidate_indicators}
    assert concepts_seen == {"GDP per capita", "unemployment"}


def test_build_query_plan_with_no_catalog_matches_has_empty_candidates():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(concepts=("nonexistent statistical concept xyz",))

    plan = build_query_plan("xyz", interpretation, engine)

    assert plan.candidate_indicators == ()


def test_query_plan_as_dict_is_json_friendly():
    plan = QueryPlan(
        question="q",
        concepts=("gdp",),
        candidate_indicators=(
            CandidateIndicator(indicator_id="X", source_id="WB_WDI", name="GDP", concept="gdp"),
        ),
        geographies=("AFG",),
        assumptions=("inferred real GDP",),
    )

    payload = plan.as_dict()
    assert payload["question"] == "q"
    assert payload["candidate_indicators"] == [
        {"indicator_id": "X", "source_id": "WB_WDI", "name": "GDP", "concept": "gdp"}
    ]
    assert payload["selected_indicators"] == []
    assert payload["validation_notes"] == []
    assert payload["assumptions"] == ["inferred real GDP"]


def test_question_interpretation_from_dict_defaults_missing_fields():
    interpretation = QuestionInterpretation.from_dict({"concepts": ["gdp"]})

    assert interpretation.concepts == ("gdp",)
    assert interpretation.geographies == ()
    assert interpretation.comparison is None
    assert interpretation.ranking is False
    assert interpretation.output_type == "table"
    assert interpretation.needs_clarification is False

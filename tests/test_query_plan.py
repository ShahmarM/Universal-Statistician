from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import (
    CandidateIndicator,
    QuestionInterpretation,
    QueryPlan,
    TransformationSpec,
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
    assert len(payload["candidate_indicators"]) == 1
    candidate = payload["candidate_indicators"][0]
    assert candidate["indicator_id"] == "X"
    assert candidate["source_id"] == "WB_WDI"
    assert candidate["name"] == "GDP"
    assert candidate["concept"] == "gdp"
    assert candidate["unit"] is None
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


# ---- TransformationSpec: structured "WHAT to compute", never a value -----------


def test_transformation_spec_from_dict_parses_a_structured_operation():
    spec = TransformationSpec.from_dict(
        {
            "operation": "share",
            "numerator_concept": "non-oil GDP",
            "denominator_concept": "total GDP",
            "output_name": "non-oil share of GDP",
        }
    )

    assert spec.operation == "share"
    assert spec.numerator_concept == "non-oil GDP"
    assert spec.denominator_concept == "total GDP"
    assert spec.output_name == "non-oil share of GDP"
    assert spec.concepts_referenced() == ("non-oil GDP", "total GDP")


def test_transformation_spec_from_dict_accepts_a_bare_string_for_backward_compatibility():
    spec = TransformationSpec.from_dict("cumulative_growth")

    assert spec.operation == "cumulative_growth"
    assert spec.concepts_referenced() == ()


def test_transformation_spec_concepts_referenced_covers_every_operation_kind():
    index_spec = TransformationSpec(operation="index", input_concept="real GDP", base_period="2015")
    assert index_spec.concepts_referenced() == ("real GDP",)

    diff_spec = TransformationSpec(operation="difference", left_concept="A", right_concept="B")
    assert diff_spec.concepts_referenced() == ("A", "B")

    # weighted_average's `inputs` are geography codes, never concepts.
    wavg_spec = TransformationSpec(operation="weighted_average", inputs=("DEU", "FRA"), weights=(1.0, 2.0))
    assert wavg_spec.concepts_referenced() == ()


def test_transformation_spec_as_dict_round_trips_through_from_dict():
    original = TransformationSpec(
        operation="index", input_concept="real GDP", base_period="2015", base_value=100.0
    )

    restored = TransformationSpec.from_dict(original.as_dict())

    assert restored == original


def test_question_interpretation_from_dict_parses_structured_transformations():
    interpretation = QuestionInterpretation.from_dict(
        {
            "concepts": ["GDP"],
            "transformations": [
                {"operation": "index", "input_concept": "GDP", "base_period": "2015"},
                "rank",  # bare-string form still accepted
            ],
        }
    )

    assert len(interpretation.transformations) == 2
    assert interpretation.transformations[0].operation == "index"
    assert interpretation.transformations[0].input_concept == "GDP"
    assert interpretation.transformations[1].operation == "rank"


def test_build_query_plan_resolves_concepts_referenced_only_inside_a_transformation():
    # The planner named "Unemployment rate" only inside a share
    # transformation, not in the top-level concepts list - build_query_plan
    # must still resolve it through the catalog, and select_indicators()
    # (which loops over plan.concepts) must be able to select it.
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(
        concepts=("GDP per capita",),
        geographies=("AFG",),
        transformations=(
            TransformationSpec(
                operation="share",
                numerator_concept="Unemployment rate",
                denominator_concept="GDP per capita",
            ),
        ),
    )

    plan = build_query_plan("share question", interpretation, engine)

    assert "Unemployment rate" in plan.concepts
    assert "GDP per capita" in plan.concepts
    concepts_seen = {c.concept for c in plan.candidate_indicators}
    assert "Unemployment rate" in concepts_seen


# ---- Phase G: geography resolution (a real LLM planner routinely writes a
# country NAME rather than the code a provider needs - see
# docs/benchmarks/phase-g-live-planner-report.md) --------------------------


def test_build_query_plan_resolves_country_names_to_alpha_3_codes():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(
        concepts=("GDP per capita",), geographies=("Azerbaijan", "Georgia")
    )

    plan = build_query_plan("q", interpretation, engine)

    assert plan.geographies == ("AZE", "GEO")


def test_build_query_plan_leaves_already_correct_codes_unchanged():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(concepts=("GDP per capita",), geographies=("AFG", "USA"))

    plan = build_query_plan("q", interpretation, engine)

    assert plan.geographies == ("AFG", "USA")


def test_build_query_plan_resolves_weighted_average_inputs_as_geographies():
    engine = _engine_with_catalog()
    interpretation = QuestionInterpretation(
        concepts=("GDP per capita",),
        geographies=("Germany", "France"),
        transformations=(
            TransformationSpec(
                operation="weighted_average", inputs=("Germany", "France"), weights=(1.0, 2.0)
            ),
        ),
    )

    plan = build_query_plan("q", interpretation, engine)

    assert plan.transformations[0].inputs == ("DEU", "FRA")


def test_question_interpretation_from_dict_treats_latest_as_a_missing_period():
    # Caught live: AnthropicPlanner wrote end_period="latest" for "...to the
    # latest available year" instead of leaving it null.
    interpretation = QuestionInterpretation.from_dict(
        {"concepts": ["GDP"], "start_period": "2015", "end_period": "latest"}
    )

    assert interpretation.start_period == "2015"
    assert interpretation.end_period is None


def test_question_interpretation_from_dict_cleans_transformation_base_period_too():
    interpretation = QuestionInterpretation.from_dict(
        {
            "concepts": ["GDP"],
            "transformations": [{"operation": "index", "input_concept": "GDP", "base_period": "Present"}],
        }
    )

    assert interpretation.transformations[0].base_period is None

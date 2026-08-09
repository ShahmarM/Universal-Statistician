"""Offline tests for the trusted statistical tool layer (agent/tools.py,
Phase 1) — no LLM involved, every tool called directly with plain
arguments, exactly the shape an LLM's tool_use block would supply."""

from __future__ import annotations

from universal_statistician.agent import tools as agent_tools
from universal_statistician.agent.state import InvestigationState, catalog_id
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
            ("SP_POP_TOTL", "AZE"): make_series(
                "SP_POP_TOTL", "AZE", {"2022": 10.1, "2023": 10.2}, source_id="WB_WDI"
            ),
        },
    )
    provider_geo = LookupProvider(
        "IMF_DATA_CPI",
        {("NGDP", "AZE"): make_series("NGDP", "AZE", {"2022": 80.0, "2023": 75.0}, source_id="IMF_DATA_CPI")},
    )
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_CD",
                source_id="WB_WDI",
                names={"en": "GDP (current US$)"},
                unit="current US$",
                frequency="A",
                geographic_coverage=("AZE", "GEO"),
                semantics=StatisticalSemantics(price_basis="nominal", currency="USD"),
                dataset_id="WDI",
                source_organization="World Bank",
                official_url="https://data.worldbank.org",
            ),
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_KD",
                source_id="WB_WDI",
                names={"en": "GDP (constant 2015 US$)"},
                unit="constant 2015 US$",
                frequency="A",
                geographic_coverage=("AZE", "GEO"),
                semantics=StatisticalSemantics(price_basis="real", currency="USD"),
                dataset_id="WDI",
                source_organization="World Bank",
            ),
            IndicatorEntry(
                indicator_id="SP_POP_TOTL",
                source_id="WB_WDI",
                names={"en": "Population, total"},
                unit="persons",
                frequency="A",
                geographic_coverage=("AZE", "GEO"),
                dataset_id="WDI",
                source_organization="World Bank",
            ),
            IndicatorEntry(
                indicator_id="NGDP",
                source_id="IMF_DATA_CPI",
                names={"en": "GDP, current prices"},
                unit="national currency",
                frequency="A",
                geographic_coverage=("AZE",),
                dataset_id="CPI",
                source_organization="IMF",
            ),
        ]
    )
    return QueryEngine({"WB_WDI": provider, "IMF_DATA_CPI": provider_geo}, catalog=catalog)


def _state() -> InvestigationState:
    return InvestigationState(question="q", engine=_engine())


# ---- search_series --------------------------------------------------------


def test_search_series_returns_candidates_and_records_them():
    state = _state()
    result = agent_tools.search_series(state, query="GDP", limit=5)

    assert result["count"] >= 2
    ids = {c["catalog_id"] for c in result["candidates"]}
    assert catalog_id("WB_WDI", "NY_GDP_MKTP_CD") in ids
    assert catalog_id("WB_WDI", "NY_GDP_MKTP_KD") in ids
    assert len(state.candidates_considered) == result["count"]


def test_search_series_respects_source_preference():
    state = _state()
    result = agent_tools.search_series(state, query="GDP", source_preference="IMF_DATA_CPI", limit=5)
    assert all(c["source_id"] == "IMF_DATA_CPI" for c in result["candidates"])


def test_search_series_does_not_duplicate_candidates_across_calls():
    state = _state()
    agent_tools.search_series(state, query="GDP", limit=5)
    agent_tools.search_series(state, query="GDP", limit=5)
    ids = [c.catalog_id for c in state.candidates_considered]
    assert len(ids) == len(set(ids))


# ---- inspect_series --------------------------------------------------------


def test_inspect_series_returns_full_metadata():
    state = _state()
    result = agent_tools.inspect_series(state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_KD"))

    assert result["found"] is True
    assert result["unit"] == "constant 2015 US$"
    assert result["semantics"]["price_basis"] == "real"
    assert result["geographic_coverage"] == ["AZE", "GEO"]


def test_inspect_series_unknown_catalog_id_is_not_found():
    state = _state()
    result = agent_tools.inspect_series(state, catalog_id=catalog_id("WB_WDI", "NOPE"))
    assert result["found"] is False


# ---- check_coverage --------------------------------------------------------


def test_check_coverage_reports_missing_geography():
    state = _state()
    result = agent_tools.check_coverage(
        state,
        catalog_ids=[catalog_id("WB_WDI", "NY_GDP_MKTP_CD")],
        geographies=["AZE", "USA"],
    )
    entry = result["results"][0]
    assert entry["available_geographies"] == ["AZE"]
    assert entry["missing_geographies"] == ["USA"]


def test_check_coverage_flags_frequency_mismatch():
    state = _state()
    result = agent_tools.check_coverage(
        state,
        catalog_ids=[catalog_id("WB_WDI", "NY_GDP_MKTP_CD")],
        geographies=["AZE"],
        frequency="Q",
    )
    entry = result["results"][0]
    assert entry["frequency_compatible"] is False
    assert any("frequency" in w.lower() for w in entry["warnings"])


# ---- retrieve_series --------------------------------------------------------


def test_retrieve_series_populates_state_and_returns_summary():
    state = _state()
    result = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), geographies=["AZE"]
    )
    outcome = result["results"][0]
    assert outcome["ok"] is True
    result_id = outcome["result_id"]
    assert result_id in state.retrieved
    assert state.table.value_at("2022", result_id) == 78.8
    assert outcome["latest_period"] == "2023"
    assert outcome["attribution"]["source_id"] == "WB_WDI"


def test_retrieve_series_handles_a_missing_geography_without_crashing():
    state = _state()
    result = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), geographies=["USA"]
    )
    outcome = result["results"][0]
    assert outcome["ok"] is False
    assert state.warnings


def test_retrieve_series_resolves_country_names_to_codes():
    state = _state()
    result = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), geographies=["Azerbaijan"]
    )
    assert result["results"][0]["ok"] is True
    assert result["results"][0]["geography"] == "AZE"


# ---- compare_series --------------------------------------------------------


def test_compare_series_flags_price_basis_and_unit_differences():
    state = _state()
    r1 = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), geographies=["AZE"]
    )["results"][0]["result_id"]
    r2 = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_KD"), geographies=["AZE"]
    )["results"][0]["result_id"]

    result = agent_tools.compare_series(state, result_ids=[r1, r2])

    assert result["units_compatible"] is False
    assert result["price_bases"] == ["nominal", "real"]
    assert result["numeric_comparison"]["overlapping_period_count"] == 4
    assert "disclaimer" in result


def test_compare_series_requires_at_least_two_results():
    state = _state()
    r1 = agent_tools.retrieve_series(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), geographies=["AZE"]
    )["results"][0]["result_id"]
    result = agent_tools.compare_series(state, result_ids=[r1])
    assert "error" in result


def test_compare_series_rejects_a_catalog_id_used_as_a_result_id():
    state = _state()
    result = agent_tools.compare_series(
        state, result_ids=["WB_WDI::NY_GDP_MKTP_CD", "result_1"]
    )
    assert "error" in result


# ---- calculate --------------------------------------------------------


def _retrieve(state, ind, geo="AZE", source="WB_WDI"):
    return agent_tools.retrieve_series(
        state, catalog_id=catalog_id(source, ind), geographies=[geo]
    )["results"][0]["result_id"]


def test_calculate_growth_produces_a_derived_result_with_lineage():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")

    result = agent_tools.calculate(state, operation="growth", input=r1)

    assert "result_id" in result
    result_id = result["result_id"]
    assert result_id in state.derived
    assert state.derived[result_id].input_result_ids == (r1,)
    assert result["formula"]
    assert result["values"]  # at least one period computed


def test_calculate_cagr_over_a_range():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.calculate(state, operation="cagr", input=r1, start_period="2020", end_period="2023")
    assert "result_id" in result
    assert list(result["values"].keys()) == ["2023"]


def test_calculate_index_rebases_to_100():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.calculate(state, operation="index", input=r1, base_period="2020")
    assert result["values"]["2020"] == 100.0


def test_calculate_share_of_two_results():
    state = _state()
    gdp = _retrieve(state, "NY_GDP_MKTP_CD")
    pop = _retrieve(state, "SP_POP_TOTL")
    result = agent_tools.calculate(state, operation="share", numerator=gdp, denominator=pop)
    assert "result_id" in result
    assert state.derived[result["result_id"]].input_result_ids == (gdp, pop)


def test_calculate_weighted_average():
    state = _state()
    a = _retrieve(state, "NY_GDP_MKTP_CD")
    b = _retrieve(state, "NY_GDP_MKTP_KD")
    result = agent_tools.calculate(state, operation="weighted_average", weights={a: 0.7, b: 0.3})
    assert "result_id" in result
    assert "2022" in result["values"]


def test_calculate_rank_returns_one_result_per_input():
    state = _state()
    a = _retrieve(state, "NY_GDP_MKTP_CD")
    b = _retrieve(state, "NY_GDP_MKTP_KD")
    result = agent_tools.calculate(state, operation="rank", inputs=[a, b])
    assert len(result["results"]) == 2
    for entry in result["results"]:
        # every value should be 1 or 2 (two-way ranking)
        values = {p: state.table.value_at(p, entry["result_id"]) for p in state.table.periods()}
        assert all(v in (1.0, 2.0) for v in values.values() if v is not None)


def test_calculate_rejects_unknown_operation():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.calculate(state, operation="do_something_arbitrary", input=r1)
    assert "error" in result


def test_calculate_rejects_an_invented_result_id():
    state = _state()
    result = agent_tools.calculate(state, operation="growth", input="result_999")
    assert "error" in result


def test_calculate_growth_with_insufficient_data_warns_instead_of_crashing():
    state = _state()
    # single-observation series: growth needs at least two periods.
    single_obs_engine = _engine()
    single_obs_engine._catalog.add(
        [
            IndicatorEntry(
                indicator_id="ONE_POINT",
                source_id="WB_WDI",
                names={"en": "One point"},
                dataset_id="WDI",
            )
        ]
    )
    state = InvestigationState(question="q", engine=single_obs_engine)
    provider = single_obs_engine._providers["WB_WDI"]
    provider._table[("ONE_POINT", "AZE")] = make_series("ONE_POINT", "AZE", {"2023": 5.0}, source_id="WB_WDI")
    r1 = _retrieve(state, "ONE_POINT")
    result = agent_tools.calculate(state, operation="growth", input=r1)
    assert "error" not in result
    assert result["values"] == {}
    assert "warning" in result
    assert state.warnings


# ---- validate --------------------------------------------------------


def test_validate_passes_for_clean_retrieved_data():
    state = _state()
    _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.validate(state)
    assert result["status"] in {"PASS", "WARNING"}
    assert state.validation_results


def test_validate_warns_on_missing_requested_geography():
    state = _state()
    _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.validate(state, requested_geographies=["USA"])
    assert result["status"] in {"WARNING", "FAIL"}


def test_validate_rejects_unknown_result_id():
    state = _state()
    result = agent_tools.validate(state, result_ids=["result_999"])
    assert "error" in result


# ---- inspect_provenance --------------------------------------------------


def test_inspect_provenance_resolves_a_base_result():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")
    result = agent_tools.inspect_provenance(state, result_id=r1, period="2022")
    assert result["kind"] == "observation"
    assert result["value"] == 78.8
    assert result["source_id"] == "WB_WDI"
    assert state.provenance_references


def test_inspect_provenance_resolves_a_derived_result_to_its_inputs():
    state = _state()
    r1 = _retrieve(state, "NY_GDP_MKTP_CD")
    calc = agent_tools.calculate(state, operation="cumulative_growth", input=r1, start_period="2020", end_period="2023")
    result = agent_tools.inspect_provenance(state, result_id=calc["result_id"])
    assert result["kind"] == "derived"
    assert result["inputs"]
    assert all(i["kind"] == "observation" for i in result["inputs"])


# ---- reject_candidate --------------------------------------------------


def test_reject_candidate_records_a_reason():
    state = _state()
    agent_tools.reject_candidate(
        state, catalog_id=catalog_id("WB_WDI", "NY_GDP_MKTP_CD"), reason="nominal, not real GDP"
    )
    assert len(state.candidates_rejected) == 1
    assert state.candidates_rejected[0].reason == "nominal, not real GDP"


# ---- dispatch_tool ----------------------------------------------------


def test_dispatch_tool_routes_to_the_right_function():
    state = _state()
    result = agent_tools.dispatch_tool(state, "search_series", {"query": "GDP"})
    assert "candidates" in result


def test_dispatch_tool_reports_an_unknown_tool_cleanly():
    state = _state()
    result = agent_tools.dispatch_tool(state, "delete_everything", {})
    assert "error" in result


def test_dispatch_tool_reports_bad_arguments_cleanly():
    state = _state()
    result = agent_tools.dispatch_tool(state, "search_series", {"not_a_real_argument": 1})
    assert "error" in result


def test_timed_dispatch_tool_returns_a_duration():
    state = _state()
    result, duration_ms = agent_tools.timed_dispatch_tool(state, "search_series", {"query": "GDP"})
    assert "candidates" in result
    assert duration_ms >= 0


# ---- tool schemas ----------------------------------------------------


def test_tool_schemas_match_tool_functions_exactly():
    schema_names = {schema["name"] for schema in agent_tools.TOOL_SCHEMAS}
    assert schema_names == set(agent_tools.TOOL_FUNCTIONS)


def test_every_tool_schema_has_a_description_and_object_input_schema():
    for schema in agent_tools.TOOL_SCHEMAS:
        assert schema["description"]
        assert schema["input_schema"]["type"] == "object"

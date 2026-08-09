from __future__ import annotations

from universal_statistician.core.query_plan import CandidateIndicator, QueryPlan
from universal_statistician.core.selection import select_indicators


def _plan(**kwargs) -> QueryPlan:
    defaults = dict(question="q", concepts=("gdp",), candidate_indicators=())
    defaults.update(kwargs)
    return QueryPlan(**defaults)


def test_selects_the_only_candidate_for_a_single_concept():
    candidate = CandidateIndicator(indicator_id="X", source_id="WB_WDI", name="GDP", concept="gdp")
    plan = _plan(concepts=("gdp",), candidate_indicators=(candidate,))

    result = select_indicators(plan)

    assert result.selected_indicators == (candidate,)
    assert any("Selected WB_WDI/X" in a for a in result.assumptions)


def test_exact_name_match_beats_fuzzy_match():
    exact = CandidateIndicator(indicator_id="EXACT", source_id="WB_WDI", name="GDP per capita", concept="gdp per capita")
    fuzzy = CandidateIndicator(indicator_id="FUZZY", source_id="WB_WDI", name="Gross domestic product growth", concept="gdp per capita")
    plan = _plan(concepts=("gdp per capita",), candidate_indicators=(fuzzy, exact))

    result = select_indicators(plan)

    assert result.selected_indicators == (exact,)


def test_geographic_coverage_of_requested_areas_wins():
    covers_all = CandidateIndicator(
        indicator_id="COVERS", source_id="A", name="GDP", concept="gdp",
        geographic_coverage=("AFG", "USA"),
    )
    covers_none = CandidateIndicator(
        indicator_id="MISSES", source_id="B", name="GDP", concept="gdp",
        geographic_coverage=("DEU", "FRA"),
    )
    plan = _plan(concepts=("gdp",), geographies=("AFG", "USA"), candidate_indicators=(covers_none, covers_all))

    result = select_indicators(plan)

    assert result.selected_indicators == (covers_all,)


def test_unknown_geographic_coverage_is_not_penalized():
    # No geographic_coverage recorded (not yet discovered) must not score
    # worse than a source whose coverage explicitly excludes the request.
    unknown = CandidateIndicator(indicator_id="UNKNOWN", source_id="A", name="GDP", concept="gdp")
    excludes = CandidateIndicator(
        indicator_id="EXCLUDES", source_id="B", name="GDP", concept="gdp",
        geographic_coverage=("DEU",),
    )
    plan = _plan(concepts=("gdp",), geographies=("AFG",), candidate_indicators=(excludes, unknown))

    result = select_indicators(plan)

    assert result.selected_indicators == (unknown,)


def test_frequency_match_is_preferred():
    matching = CandidateIndicator(indicator_id="M", source_id="A", name="CPI", concept="cpi", frequency="M")
    annual = CandidateIndicator(indicator_id="A", source_id="A", name="CPI", concept="cpi", frequency="A")
    plan = _plan(concepts=("cpi",), frequency="M", candidate_indicators=(annual, matching))

    result = select_indicators(plan)

    assert result.selected_indicators == (matching,)


def test_concept_with_no_candidates_is_noted_not_silently_dropped():
    plan = _plan(concepts=("nonexistent",), candidate_indicators=())

    result = select_indicators(plan)

    assert result.selected_indicators == ()
    assert any("No catalog candidates found for 'nonexistent'" in a for a in result.assumptions)


def test_selection_is_deterministic_on_ties():
    tie_a = CandidateIndicator(indicator_id="B_IND", source_id="SRC", name="X", concept="x")
    tie_b = CandidateIndicator(indicator_id="A_IND", source_id="SRC", name="X", concept="x")
    plan = _plan(concepts=("x",), candidate_indicators=(tie_a, tie_b))

    result = select_indicators(plan)

    assert result.selected_indicators == (tie_b,)  # "A_IND" sorts before "B_IND"


def test_warns_when_concepts_resolve_to_different_sources():
    gdp = CandidateIndicator(indicator_id="GDP", source_id="WB_WDI", name="GDP", concept="gdp")
    unemployment = CandidateIndicator(
        indicator_id="UNEMP", source_id="ESTAT_NAMA_10_GDP", name="Unemployment", concept="unemployment"
    )
    plan = _plan(concepts=("gdp", "unemployment"), candidate_indicators=(gdp, unemployment))

    result = select_indicators(plan)

    assert set(result.selected_indicators) == {gdp, unemployment}
    assert any("different sources" in a for a in result.assumptions)


def test_no_warning_when_concepts_share_one_source():
    gdp = CandidateIndicator(indicator_id="GDP", source_id="WB_WDI", name="GDP", concept="gdp")
    pop = CandidateIndicator(indicator_id="POP", source_id="WB_WDI", name="Population", concept="population")
    plan = _plan(concepts=("gdp", "population"), candidate_indicators=(gdp, pop))

    result = select_indicators(plan)

    assert not any("different sources" in a for a in result.assumptions)

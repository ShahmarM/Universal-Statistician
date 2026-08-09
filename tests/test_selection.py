from __future__ import annotations

from universal_statistician.core.models import StatisticalSemantics
from universal_statistician.core.query_plan import CandidateIndicator, QueryPlan
from universal_statistician.core.selection import score_candidate, select_indicators


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


def test_catalog_search_rank_breaks_ties_between_equally_named_candidates():
    # Phase H: reproduces a real, live-discovered bug. Both candidates'
    # names contain "inflation" (one as the actual concept, one as an
    # unrelated compound modifier: "...inflation-adjusted dollars"), and
    # neither carries geographic_coverage/frequency data to differentiate
    # on — before search_rank existed, this was an exact score tie broken
    # alphabetically by source_id, which picked US_CENSUS_ACS1's noise
    # entry over WB_WDI's flagship indicator purely because "US..." sorts
    # before "WB...". Catalog.search() (Phase C) already ranked the real
    # match first; search_rank is what lets selection see that.
    flagship = CandidateIndicator(
        indicator_id="FP_CPI_TOTL_ZG",
        source_id="WB_WDI",
        name="Inflation, consumer prices (annual %)",
        concept="inflation",
        search_rank=0,
    )
    noise = CandidateIndicator(
        indicator_id="B19313C_001E",
        source_id="US_CENSUS_ACS1",
        name="Estimate!!Aggregate income in the past 12 months (in 2022 inflation-adjusted dollars)",
        concept="inflation",
        search_rank=3,
    )
    plan = _plan(concepts=("inflation",), candidate_indicators=(noise, flagship))

    result = select_indicators(plan)

    assert result.selected_indicators == (flagship,)


def test_word_order_does_not_block_the_name_match_bonus():
    # Live-discovered bug: the name-match bonus was a literal substring
    # check, so concept "total population" did not match candidate name
    # "Population, total" (the words are the same, just reordered) even
    # though a human would call that an exact match.
    reordered = CandidateIndicator(
        indicator_id="SP_POP_TOTL", source_id="WB_WDI", name="Population, total", concept="total population",
    )
    plan = _plan(concepts=("total population",), candidate_indicators=(reordered,))

    _, reasons = score_candidate(reordered, plan)

    assert any("every word of the requested concept" in r for r in reasons)


def test_a_demographic_subgroup_share_does_not_outrank_the_real_total():
    # Live-discovered bug, the actual root cause of a wrong benchmark
    # answer ("Azerbaijan's population was 50.98"): for concept "total
    # population", "Population, female (% of total population)" contains
    # every one of the concept's words (so it got the same name-match bonus
    # as "Population, total") with nothing penalizing the fact that it
    # measures a completely different thing -- a demographic subgroup's
    # share, not a population count. Without a qualifier penalty this
    # subgroup-share series could outrank (or, as a retrieval fallback,
    # get chosen over) the real total.
    total = CandidateIndicator(
        indicator_id="SP_POP_TOTL", source_id="WB_WDI", name="Population, total",
        concept="total population", search_rank=0,
    )
    female_share = CandidateIndicator(
        indicator_id="SP_POP_TOTL_FE_ZS", source_id="WB_WDI",
        name="Population, female (% of total population)",
        concept="total population", search_rank=1,
    )
    plan = _plan(concepts=("total population",), candidate_indicators=(female_share, total))

    result = select_indicators(plan)

    assert result.selected_indicators == (total,)


def test_a_qualifier_word_already_present_in_the_concept_is_not_penalized():
    # The qualifier penalty is symmetric: if the concept itself asks for a
    # subgroup ("female population"), a candidate naming that subgroup must
    # not be punished for it.
    female = CandidateIndicator(
        indicator_id="SP_POP_TOTL_FE_ZS", source_id="WB_WDI",
        name="Population, female (% of total population)", concept="female population",
    )
    plan = _plan(concepts=("female population",), candidate_indicators=(female,))

    score, reasons = score_candidate(female, plan)

    assert not any("does not ask for" in r for r in reasons)


def test_percentage_concepts_are_not_penalized_for_being_percentages():
    # The qualifier penalty is deliberately narrow: "%"/"rate"/"growth" are
    # NOT qualifier markers, because they are the natural unit for many
    # concepts (inflation, unemployment) without the concept text spelling
    # it out -- penalizing them would wrongly punish the correct candidate
    # for exactly the concepts this project cares most about.
    inflation = CandidateIndicator(
        indicator_id="FP_CPI_TOTL_ZG", source_id="WB_WDI",
        name="Inflation, consumer prices (annual %)", concept="inflation",
    )
    plan = _plan(concepts=("inflation",), candidate_indicators=(inflation,))

    _, reasons = score_candidate(inflation, plan)

    assert not any("does not ask for" in r for r in reasons)


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


def test_documented_semantics_earns_a_small_score_bonus():
    plan = _plan(concepts=("gdp",))
    with_semantics = CandidateIndicator(
        indicator_id="X", source_id="A", name="GDP", concept="gdp",
        semantics=StatisticalSemantics(price_basis="nominal"),
    )
    without_semantics = CandidateIndicator(indicator_id="Y", source_id="A", name="GDP", concept="gdp")

    score_with, reasons_with = score_candidate(with_semantics, plan)
    score_without, _ = score_candidate(without_semantics, plan)

    assert score_with > score_without
    assert any("semantics" in r for r in reasons_with)


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

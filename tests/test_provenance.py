from __future__ import annotations

from datetime import datetime, timezone

import pytest

from universal_statistician.core.compose import (
    ComparisonColumn,
    build_comparison,
    compare_across_countries,
    with_difference,
    with_growth,
    with_rank,
    with_ratio,
)
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import Attribution
from universal_statistician.core.provenance import (
    DerivedProvenance,
    ObservationProvenance,
    resolve_provenance,
)

from .helpers import LookupProvider, make_series as _series


def _attribution(source_id="WB_WDI") -> Attribution:
    return Attribution(
        source_id=source_id,
        source_name="World Bank — World Development Indicators",
        dataset_id="WDI",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_url="https://api.worldbank.org",
    )


# ---- base (observation) provenance -----------------------------------------


def test_resolve_provenance_for_a_base_column_matches_section_17s_example_fields():
    provider = LookupProvider(
        "WB_WDI", {("NY_GDP_PCAP_CD", "AZE"): _series("NY_GDP_PCAP_CD", "AZE", {"2025": 7130.0}, source_id="WB_WDI")}
    )
    engine = QueryEngine({"WB_WDI": provider})
    table = compare_across_countries(engine, "WB_WDI", "NY_GDP_PCAP_CD", ["AZE"])

    prov = resolve_provenance(table, "AZE", "2025")

    assert isinstance(prov, ObservationProvenance)
    assert prov.indicator_id == "NY_GDP_PCAP_CD"
    assert prov.ref_area == "AZE"
    assert prov.period == "2025"
    assert prov.value == 7130.0
    assert prov.source_id == "WB_WDI"
    assert prov.retrieved_at


def test_resolve_provenance_raises_for_a_base_column_without_attribution():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=None)
    table = build_comparison([(col, _series("POP", "AFG", {"2020": 10.0}))])

    with pytest.raises(ValueError, match="no attribution"):
        resolve_provenance(table, "AFG", "2020")


# ---- same-period derived provenance (exact) --------------------------------


def test_resolve_provenance_for_a_ratio_is_exact_with_no_note():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = with_ratio(
        build_comparison(
            [
                (col_a, _series("POP", "AFG", {"2020": 10.0})),
                (col_b, _series("POP", "USA", {"2020": 200.0})),
            ]
        ),
        baseline_key="USA",
    )

    prov = resolve_provenance(table, "AFG__ratio_to_USA", "2020")

    assert isinstance(prov, DerivedProvenance)
    assert prov.note is None
    assert prov.formula
    assert prov.calculated_by == "Universal Statistician"
    assert {i.column_key for i in prov.inputs} == {"AFG", "USA"}
    assert all(isinstance(i, ObservationProvenance) and i.period == "2020" for i in prov.inputs)


def test_resolve_provenance_for_rank_includes_every_base_column_at_that_period():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = with_rank(
        build_comparison(
            [
                (col_a, _series("POP", "AFG", {"2020": 10.0})),
                (col_b, _series("POP", "USA", {"2020": 300.0})),
            ]
        )
    )

    prov = resolve_provenance(table, "USA__rank", "2020")

    assert prov.note is None
    assert {i.column_key for i in prov.inputs} == {"AFG", "USA"}


# ---- multi-period derived provenance (honest fallback) ----------------------


def test_resolve_provenance_for_growth_notes_multi_period_dependency():
    col = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    table = with_growth(
        build_comparison([(col, _series("POP", "AFG", {"2019": 100.0, "2020": 110.0}))])
    )

    prov = resolve_provenance(table, "AFG__yoy_growth_pct", "2020")

    assert prov.note is not None
    assert "more than one period" in prov.note
    periods_seen = {i.period for i in prov.inputs}
    assert periods_seen == {"2019", "2020"}  # both contributing periods present


# ---- recursive chains --------------------------------------------------------


def test_resolve_provenance_recurses_through_a_derived_input():
    # None of compose.py's transformations read derived columns as their own
    # base-column input (with_growth/with_rank operate on base columns
    # only) — but with_difference() takes arbitrary column keys, so two
    # already-computed ratios is a real, reachable derived-from-derived case.
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="GEO", label="GEO", attribution=_attribution())
    col_c = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = build_comparison(
        [
            (col_a, _series("POP", "AFG", {"2020": 10.0})),
            (col_b, _series("POP", "GEO", {"2020": 20.0})),
            (col_c, _series("POP", "USA", {"2020": 300.0})),
        ]
    )
    table = with_ratio(table, baseline_key="USA")
    table = with_difference(table, "AFG__ratio_to_USA", "GEO__ratio_to_USA")

    prov = resolve_provenance(table, "AFG__ratio_to_USA__minus_GEO__ratio_to_USA", "2020")

    assert isinstance(prov, DerivedProvenance)
    assert prov.note is None  # difference is a same-period operation
    by_key = {i.column_key: i for i in prov.inputs}
    assert set(by_key) == {"AFG__ratio_to_USA", "GEO__ratio_to_USA"}
    # each ratio's own provenance recurses down to real observations
    for nested in by_key.values():
        assert isinstance(nested, DerivedProvenance)
        assert all(isinstance(i, ObservationProvenance) for i in nested.inputs)
    assert {i.column_key for i in by_key["AFG__ratio_to_USA"].inputs} == {"AFG", "USA"}
    assert {i.column_key for i in by_key["GEO__ratio_to_USA"].inputs} == {"GEO", "USA"}


# ---- serialization ------------------------------------------------------------


def test_provenance_as_dict_is_json_friendly_and_nests():
    col_a = ComparisonColumn(key="AFG", label="AFG", attribution=_attribution())
    col_b = ComparisonColumn(key="USA", label="USA", attribution=_attribution())
    table = with_ratio(
        build_comparison(
            [
                (col_a, _series("POP", "AFG", {"2020": 10.0})),
                (col_b, _series("POP", "USA", {"2020": 200.0})),
            ]
        ),
        baseline_key="USA",
    )

    payload = resolve_provenance(table, "AFG__ratio_to_USA", "2020").as_dict()

    assert payload["kind"] == "derived"
    assert len(payload["inputs"]) == 2
    assert all(i["kind"] == "observation" for i in payload["inputs"])
    assert payload["inputs"][0]["source_id"]

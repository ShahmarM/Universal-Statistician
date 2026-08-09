"""Benchmark-style natural-language tests (section 25): the task
description's own example questions, each run through the full /ask
pipeline (core/ask.py::answer_question).

No live LLM is available in this sandbox (no ANTHROPIC_API_KEY), so each
benchmark supplies a *scripted* QuestionInterpretation standing in for what
a working AnthropicPlanner should produce for that question — the same
approach test_ask.py already uses (ScriptedPlanner), and exactly what
section 25 asks for: "define expected: intent, appropriate indicator
family, geography, period, operation, acceptable sources, output
structure. Do not require exact numerical assertions for live external
data ... instead validate structure, provenance, and reasonable expected
series selection." This tests the *orchestration* (retrieval, selection,
transformation dispatch, validation, provenance) against realistic planner
output, against a small fake multi-country/multi-indicator catalog built
for this file (not real Azerbaijan/Georgia/Armenia data - clearly
synthetic, LookupProvider-based, the same ground-rule every other test in
this project follows for non-live data).
"""

from __future__ import annotations

from universal_statistician.core.ask import answer_question
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import QuestionInterpretation
from universal_statistician.core.validation import ValidationStatus

from .helpers import LookupProvider, make_series


class ScriptedPlanner:
    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


def _benchmark_engine() -> QueryEngine:
    provider = LookupProvider(
        "WB_WDI",
        {
            ("SP_POP_TOTL", "AZE"): make_series(
                "SP_POP_TOTL", "AZE", {"2023": 10.4}, source_id="WB_WDI"
            ),
            ("NY_GDP_MKTP_CD", "AZE"): make_series(
                "NY_GDP_MKTP_CD",
                "AZE",
                {str(y): 40.0 + y - 2010 for y in range(2010, 2026)},
                source_id="WB_WDI",
            ),
            ("FP_CPI_TOTL_ZG", "AZE"): make_series(
                "FP_CPI_TOTL_ZG", "AZE", {"2022": 13.9, "2023": 8.8}, source_id="WB_WDI"
            ),
            ("FP_CPI_TOTL_ZG", "GEO"): make_series(
                "FP_CPI_TOTL_ZG", "GEO", {"2022": 11.9, "2023": 2.5}, source_id="WB_WDI"
            ),
            ("SL_UEM_TOTL_ZS", "DEU"): make_series(
                "SL_UEM_TOTL_ZS", "DEU", {"2024": 3.4}, source_id="WB_WDI"
            ),
            ("SL_UEM_TOTL_ZS", "FRA"): make_series(
                "SL_UEM_TOTL_ZS", "FRA", {"2024": 7.5}, source_id="WB_WDI"
            ),
            ("SL_UEM_TOTL_ZS", "ITA"): make_series(
                "SL_UEM_TOTL_ZS", "ITA", {"2024": 6.8}, source_id="WB_WDI"
            ),
            ("NY_GDP_PCAP_CD", "AZE"): make_series(
                "NY_GDP_PCAP_CD", "AZE", {"2023": 8100.0}, source_id="WB_WDI"
            ),
            ("NY_GDP_PCAP_CD", "GEO"): make_series(
                "NY_GDP_PCAP_CD", "GEO", {"2023": 7700.0}, source_id="WB_WDI"
            ),
            ("NY_GDP_PCAP_CD", "ARM"): make_series(
                "NY_GDP_PCAP_CD", "ARM", {"2023": 8500.0}, source_id="WB_WDI"
            ),
        },
    )
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"}),
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_CD", source_id="WB_WDI", names={"en": "GDP (current US$)"}
            ),
            IndicatorEntry(
                indicator_id="FP_CPI_TOTL_ZG",
                source_id="WB_WDI",
                names={"en": "Inflation, consumer prices (annual %)"},
            ),
            IndicatorEntry(
                indicator_id="SL_UEM_TOTL_ZS",
                source_id="WB_WDI",
                names={"en": "Unemployment, total (% of labor force)"},
            ),
            IndicatorEntry(
                indicator_id="NY_GDP_PCAP_CD",
                source_id="WB_WDI",
                names={"en": "GDP per capita (current US$)"},
            ),
        ]
    )
    return QueryEngine({"WB_WDI": provider}, catalog=catalog)


# ---- Benchmark 1: "What is the population of Azerbaijan?" ---------------------
# Intent: single direct observation. Family: population. Geography: AZE.
# Period: latest available. Operation: none. Output: answer/table.


def test_benchmark_population_of_azerbaijan():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(concepts=("population",), geographies=("AZE",), output_type="answer")
    )

    result = answer_question(engine, "What is the population of Azerbaijan?", planner=planner)

    assert result.table is not None
    assert [c["key"] for c in result.table["columns"]] == ["AZE"]
    assert "10.4" in result.answer
    assert result.validation["status"] == ValidationStatus.PASS.value
    assert result.sources and result.sources[0]["source_id"] == "WB_WDI"
    assert result.provenance


# ---- Benchmark 2: "Show Azerbaijan GDP from 2010 to 2025." --------------------
# Intent: time series over an explicit range. Family: GDP level.
# Geography: AZE. Period: 2010-2025. Operation: none. Output: table/chart.


def test_benchmark_azerbaijan_gdp_2010_to_2025():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("GDP",),
            geographies=("AZE",),
            start_period="2010",
            end_period="2025",
            output_type="chart",
        )
    )

    result = answer_question(engine, "Show Azerbaijan GDP from 2010 to 2025.", planner=planner)

    assert result.table is not None
    periods = {row["period"] for row in result.table["rows"]}
    assert periods == {str(y) for y in range(2010, 2026)}
    assert result.chart is not None
    assert result.chart["chart_type"] == "line"


# ---- Benchmark 3: "Compare inflation in Azerbaijan and Georgia." --------------
# Intent: cross-country comparison. Family: CPI/inflation.
# Geography: AZE, GEO. Operation: cross_country. Output: comparison_table.


def test_benchmark_compare_inflation_azerbaijan_georgia():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("inflation",),
            geographies=("AZE", "GEO"),
            comparison="cross_country",
            output_type="comparison_table",
        )
    )

    result = answer_question(
        engine, "Compare inflation in Azerbaijan and Georgia.", planner=planner
    )

    assert result.table is not None
    assert {c["key"] for c in result.table["columns"]} == {"AZE", "GEO"}
    assert len(result.sources) == 1  # same source (WB_WDI) for both, not silently mixed
    assert result.validation["status"] == ValidationStatus.PASS.value


# ---- Benchmark 4: "What was cumulative GDP growth between 2015 and 2024?" -----
# Intent: derived single-value statistic over an explicit range.
# Family: GDP level -> cumulative_growth transformation. Geography: AZE
# (assumed from prior context in a real conversation; scripted explicitly
# here). Operation: cumulative_growth. Output: answer, with formula/inputs
# traceable via provenance (section 17).


def test_benchmark_cumulative_gdp_growth_2015_2024():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("GDP",),
            geographies=("AZE",),
            start_period="2015",
            end_period="2024",
            transformations=("cumulative_growth",),
            output_type="answer",
        )
    )

    result = answer_question(
        engine, "What was cumulative GDP growth between 2015 and 2024?", planner=planner
    )

    assert result.table is not None
    keys = {c["key"] for c in result.table["columns"]}
    assert "AZE__cumulative_growth_pct" in keys
    growth_column = next(
        c for c in result.table["columns"] if c["key"] == "AZE__cumulative_growth_pct"
    )
    assert growth_column["derived"] is True
    assert growth_column["formula"]
    assert growth_column["input_series"] == ["AZE"]
    # provenance for the derived value is resolvable, not just the raw levels
    derived_provenance = [p for p in result.provenance if p["column_key"] == "AZE__cumulative_growth_pct"]
    assert derived_provenance
    assert derived_provenance[0]["kind"] == "derived"


# ---- Benchmark 5: "Rank EU countries by unemployment." ------------------------
# Intent: ranking across several areas. Family: unemployment rate.
# Geography: a set of EU countries. Operation: rank. Output: table/chart.


def test_benchmark_rank_eu_countries_by_unemployment():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("unemployment",),
            geographies=("DEU", "FRA", "ITA"),
            comparison="cross_country",
            ranking=True,
            output_type="comparison_table",
        )
    )

    result = answer_question(engine, "Rank EU countries by unemployment.", planner=planner)

    assert result.table is not None
    keys = {c["key"] for c in result.table["columns"]}
    assert {"DEU__rank", "FRA__rank", "ITA__rank"} <= keys
    row_2024 = next(r for r in result.table["rows"] if r["period"] == "2024")
    # FRA has the highest unemployment (7.5) among the three -> rank 1
    assert row_2024["FRA__rank"] == 1.0


# ---- Benchmark 6: "Show GDP per capita for Azerbaijan, Georgia and Armenia." --
# Intent: cross-country comparison of a *directly published* per-capita
# indicator (matches how World Bank actually publishes NY.GDP.PCAP.CD as
# its own series, not a client-side computation from GDP/population) -
# same shape as benchmark 3, different indicator family and geography set.


def test_benchmark_gdp_per_capita_three_countries():
    engine = _benchmark_engine()
    planner = ScriptedPlanner(
        QuestionInterpretation(
            concepts=("GDP per capita",),
            geographies=("AZE", "GEO", "ARM"),
            comparison="cross_country",
            output_type="comparison_table",
        )
    )

    result = answer_question(
        engine, "Show GDP per capita for Azerbaijan, Georgia and Armenia.", planner=planner
    )

    assert result.table is not None
    assert {c["key"] for c in result.table["columns"]} == {"AZE", "GEO", "ARM"}
    assert result.chart is not None
    assert result.chart["chart_type"] == "comparison"
    assert result.validation["status"] == ValidationStatus.PASS.value
    assert not result.warnings

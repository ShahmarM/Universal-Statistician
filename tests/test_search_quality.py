"""Phase C: catalog search-ranking regression tests.

Offline, network-free counterpart to scripts/benchmark_catalog_search.py
(which needs a real, live-populated catalog and isn't part of the regular
suite). This file hardcodes a small fixture that reproduces the *exact*
real failure modes found by running search() against the live catalog
(38,789 indicators) — see docs/benchmarks/phase-c-search-quality-report.md
for the full investigation — so a future change to Catalog.search()'s
ranking can't silently regress on these without a red test:

- Plain FTS5 bm25 ranked "Personal remittances, received (% of GDP)" above
  the flagship "GDP (current US$)" for a bare "GDP" query, because bm25
  has no notion of "this is the canonical indicator".
- A World Bank entry unrelated to GDP ("Short-term debt (% of total
  reserves)") matched a "GDP" query at all only because its
  `source_organization` field is a long data-lineage citation that happens
  to contain the words "GDP estimates" as a methodology note — real,
  live-observed metadata noise.
- "population" omitted the flagship "Population, total" from the top 5
  entirely in favor of narrower age/sex breakdowns whose names happen to
  repeat "population" more often (bm25 term-frequency reward).
- "inflation" correctly ranked FP_CPI_TOTL_ZG first, but then surfaced
  several US Census income entries whose name merely contains the phrase
  "inflation-adjusted" — a real cross-source false-positive from a very
  large, unrelated dataset (US_CENSUS_ACS1: 36,632 variables).
"""

from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry


def _fixture_catalog() -> Catalog:
    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="NY_GDP_MKTP_CD",
                source_id="WB_WDI",
                names={"en": "GDP (current US$)"},
                keywords=("Economy & Growth", "NY.GDP.MKTP.CD"),
            ),
            IndicatorEntry(
                indicator_id="BX_TRF_PWKR_DT_GD_ZS",
                source_id="WB_WDI",
                names={"en": "Personal remittances, received (% of GDP)"},
                keywords=("Economy & Growth", "Financial Sector", "BX.TRF.PWKR.DT.GD.ZS"),
            ),
            IndicatorEntry(
                indicator_id="DT_DOD_DSTC_IR_ZS",
                source_id="WB_WDI",
                names={"en": "Short-term debt (% of total reserves)"},
                keywords=("Economy & Growth", "External Debt", "DT.DOD.DSTC.IR.ZS"),
                # Real, live-observed noise: WDI's source_organization field is
                # a data-lineage citation, not a short org name, and this one
                # incidentally contains the word "GDP" as part of "GDP
                # estimates" — nothing to do with the indicator's own topic.
                source_organization=(
                    "International Monetary Fund (IMF), type: Balance of Payments "
                    "Statistics Yearbook and data files; World Bank (WB), type: "
                    "GDP estimates"
                ),
            ),
            IndicatorEntry(
                indicator_id="SP_POP_TOTL",
                source_id="WB_WDI",
                names={"en": "Population, total"},
                keywords=("Health", "SP.POP.TOTL"),
            ),
            IndicatorEntry(
                indicator_id="SP_RUR_TOTL",
                source_id="WB_WDI",
                names={"en": "Rural population"},
                keywords=("Environment", "SP.RUR.TOTL"),
            ),
            IndicatorEntry(
                indicator_id="SP_POP_0014_TO_ZS",
                source_id="WB_WDI",
                names={"en": "Population ages 0-14 (% of total population)"},
                keywords=("Health", "SP.POP.0014.TO.ZS"),
            ),
            IndicatorEntry(
                indicator_id="FP_CPI_TOTL_ZG",
                source_id="WB_WDI",
                names={"en": "Inflation, consumer prices (annual %)"},
                keywords=("Economy & Growth", "FP.CPI.TOTL.ZG"),
            ),
            IndicatorEntry(
                indicator_id="NY_GDP_DEFL_KD_ZG",
                source_id="WB_WDI",
                names={"en": "Inflation, GDP deflator (annual %)"},
                keywords=("Economy & Growth", "NY.GDP.DEFL.KD.ZG"),
            ),
            IndicatorEntry(
                indicator_id="B21004_007E",
                source_id="US_CENSUS_ACS1",
                names={
                    "en": (
                        "Estimate!!Median income in the past 12 months (in 2022 "
                        "inflation-adjusted dollars)--!!Total:!!Nonveteran:!!Female"
                    )
                },
            ),
            IndicatorEntry(
                indicator_id="B19061_001E",
                source_id="US_CENSUS_ACS1",
                names={
                    "en": (
                        "Estimate!!Aggregate earnings in the past 12 months "
                        "(in 2022 inflation-adjusted dollars)"
                    )
                },
            ),
        ]
    )
    return catalog


def test_bare_gdp_query_ranks_the_flagship_indicator_first():
    catalog = _fixture_catalog()

    results = catalog.search("GDP", limit=5)

    assert results[0].indicator_id == "NY_GDP_MKTP_CD"


def test_bare_gdp_query_sinks_the_citation_text_false_positive_to_last():
    # DT_DOD_DSTC_IR_ZS only matches "GDP" via its source_organization
    # citation text, not because it's actually about GDP — it should rank
    # behind every entry whose *name* genuinely mentions GDP, not compete
    # for a top spot just because bm25 saw the token somewhere.
    catalog = _fixture_catalog()

    results = catalog.search("GDP", limit=10)

    ids = [r.indicator_id for r in results]
    assert ids[-1] == "DT_DOD_DSTC_IR_ZS"


def test_bare_population_query_ranks_the_flagship_indicator_first():
    catalog = _fixture_catalog()

    results = catalog.search("population", limit=5)

    assert results[0].indicator_id == "SP_POP_TOTL"


def test_bare_inflation_query_ranks_the_flagship_indicator_first():
    catalog = _fixture_catalog()

    results = catalog.search("inflation", limit=5)

    assert results[0].indicator_id == "FP_CPI_TOTL_ZG"


def test_bare_inflation_query_ranks_real_inflation_indicators_above_census_noise():
    # Both real WB_WDI inflation indicators should outrank both US Census
    # entries that only match because their name contains the unrelated
    # phrase "inflation-adjusted" (an income-adjustment methodology note,
    # not an inflation indicator).
    catalog = _fixture_catalog()

    results = catalog.search("inflation", limit=4)

    ids = [r.indicator_id for r in results]
    assert set(ids[:2]) == {"FP_CPI_TOTL_ZG", "NY_GDP_DEFL_KD_ZG"}
    assert set(ids[2:]) == {"B21004_007E", "B19061_001E"}

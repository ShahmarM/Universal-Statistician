#!/usr/bin/env python3
"""Phase C: catalog search-quality benchmark.

Measures Top-1/Top-3/Top-5 accuracy of `QueryEngine.search_indicator()`
(the same path `search_indicator`/`/search`/`ustat search` actually use)
against a curated list of 50+ statistical concepts, each with an
"acceptable family" of (source_id, indicator_id) pairs a reasonable answer
could resolve to.

Every code in CONCEPTS below was verified to exist in the real, live-
populated catalog via a direct `catalog.get(source_id, indicator_id)` call
before being added here — none are guessed from training-data recall (see
docs/benchmarks/phase-c-search-quality-report.md for how this list was
built, including the one code — EN_ATM_CO2E_PC — that training-data recall
got wrong: World Bank has since replaced it with EN_GHG_CO2_PC_CE_AR5).

Usage:
    USTAT_CATALOG_DB_PATH=~/.universal_statistician/catalog.db \\
        python scripts/benchmark_catalog_search.py

Requires a catalog already populated via `ustat catalog refresh` (or
USTAT_CATALOG_DB_PATH pointed at one) — this is a real-data benchmark, not
an offline unit test. See tests/test_search_quality.py for the offline
regression-test counterpart, which runs against a small hardcoded fixture
reproducing the concrete failure modes found here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from universal_statistician.core.engine import default_engine


@dataclass(frozen=True)
class ConceptCase:
    concept: str
    query: str
    acceptable: frozenset[tuple[str, str]]


def _wb(*indicator_ids: str) -> frozenset[tuple[str, str]]:
    return frozenset(("WB_WDI", i) for i in indicator_ids)


CONCEPTS: tuple[ConceptCase, ...] = (
    ConceptCase("GDP", "GDP", _wb("NY_GDP_MKTP_CD")),
    ConceptCase("real GDP", "real GDP", _wb("NY_GDP_MKTP_KD")),
    ConceptCase("nominal GDP", "nominal GDP", _wb("NY_GDP_MKTP_CD", "NY_GDP_MKTP_CN")),
    ConceptCase("GDP per capita", "GDP per capita", _wb("NY_GDP_PCAP_CD")),
    ConceptCase("real GDP per capita", "real GDP per capita", _wb("NY_GDP_PCAP_KD")),
    ConceptCase("GDP growth", "GDP growth", _wb("NY_GDP_MKTP_KD_ZG")),
    ConceptCase("GNI", "GNI", _wb("NY_GNP_MKTP_CD")),
    ConceptCase("population", "population", _wb("SP_POP_TOTL")),
    ConceptCase("population growth", "population growth", _wb("SP_POP_GROW")),
    ConceptCase("population density", "population density", _wb("EN_POP_DNST")),
    ConceptCase("urban population", "urban population", _wb("SP_URB_TOTL", "SP_URB_TOTL_IN_ZS")),
    ConceptCase("rural population", "rural population", _wb("SP_RUR_TOTL")),
    ConceptCase("inflation", "inflation", _wb("FP_CPI_TOTL_ZG")),
    ConceptCase("CPI", "consumer price index", _wb("FP_CPI_TOTL")),
    ConceptCase("unemployment", "unemployment", _wb("SL_UEM_TOTL_ZS", "SL_UEM_TOTL_NE_ZS")),
    ConceptCase(
        "youth unemployment", "youth unemployment", _wb("SL_UEM_1524_ZS", "SL_UEM_1524_NE_ZS")
    ),
    ConceptCase("labor force", "labor force", _wb("SL_TLF_TOTL_IN")),
    ConceptCase(
        "labor force participation", "labor force participation", _wb("SL_TLF_CACT_ZS")
    ),
    ConceptCase("employment", "employment to population ratio", _wb("SL_EMP_TOTL_SP_ZS")),
    ConceptCase(
        "exports",
        "exports",
        _wb("NE_EXP_GNFS_CD", "NE_EXP_GNFS_ZS") | frozenset({("ESTAT_NAMA_10_GDP", "P6")}),
    ),
    ConceptCase(
        "imports",
        "imports",
        _wb("NE_IMP_GNFS_CD", "NE_IMP_GNFS_ZS") | frozenset({("ESTAT_NAMA_10_GDP", "P7")}),
    ),
    ConceptCase(
        "government expenditure",
        "government expenditure",
        _wb("NE_CON_GOVT_ZS", "GC_XPN_TOTL_GD_ZS"),
    ),
    ConceptCase(
        "government debt",
        "government debt",
        _wb("GC_DOD_TOTL_GD_ZS", "GC_DOD_TOTL_CN") | frozenset({("ESTAT_NAMA_10_GDP", "GD")}),
    ),
    ConceptCase("current account balance", "current account balance", _wb("BN_CAB_XOKA_GD_ZS")),
    ConceptCase("external debt", "external debt", _wb("DT_DOD_DECT_CD")),
    ConceptCase("trade balance", "trade balance", _wb("BN_GSR_GNFS_CD")),
    ConceptCase(
        "foreign direct investment",
        "foreign direct investment",
        _wb("BN_KLT_DINV_CD", "BX_KLT_DINV_CD_WD", "BM_KLT_DINV_CD_WD"),
    ),
    ConceptCase("total reserves", "total reserves", _wb("FI_RES_TOTL_CD")),
    ConceptCase("exchange rate", "exchange rate", _wb("PA_NUS_FCRF")),
    ConceptCase("life expectancy", "life expectancy", _wb("SP_DYN_LE00_IN")),
    ConceptCase("fertility rate", "fertility rate", _wb("SP_DYN_TFRT_IN")),
    ConceptCase("birth rate", "birth rate", _wb("SP_DYN_CBRT_IN")),
    ConceptCase("death rate", "death rate", _wb("SP_DYN_CDRT_IN")),
    ConceptCase("infant mortality", "infant mortality", _wb("SP_DYN_IMRT_IN")),
    ConceptCase("maternal mortality", "maternal mortality", _wb("SH_STA_MMRT")),
    ConceptCase(
        "industrial production",
        "industrial production",
        _wb("NV_IND_TOTL_ZS"),
    ),
    ConceptCase("agriculture", "agriculture", _wb("NV_AGR_TOTL_ZS")),
    ConceptCase("tourism", "tourism", _wb("ST_INT_ARVL")),
    ConceptCase(
        "education expenditure", "education expenditure", _wb("SE_XPD_TOTL_GD_ZS")
    ),
    ConceptCase(
        "primary school enrollment", "primary school enrollment", _wb("SE_PRM_ENRR")
    ),
    ConceptCase(
        "tertiary school enrollment", "tertiary school enrollment", _wb("SE_TER_ENRR")
    ),
    ConceptCase("literacy rate", "literacy rate", _wb("SE_ADT_LITR_ZS")),
    ConceptCase("health expenditure", "health expenditure", _wb("SH_XPD_CHEX_GD_ZS")),
    ConceptCase("Gini index", "Gini index", _wb("SI_POV_GINI")),
    ConceptCase("poverty rate", "poverty rate", _wb("SI_POV_DDAY")),
    ConceptCase(
        "income share lowest 20%",
        "income share lowest 20 percent",
        _wb("SI_DST_FRST_20"),
    ),
    ConceptCase("internet users", "internet users", _wb("IT_NET_USER_ZS")),
    ConceptCase("broadband subscriptions", "broadband subscriptions", _wb("IT_NET_BBND")),
    ConceptCase(
        "CO2 emissions per capita",
        "CO2 emissions per capita",
        _wb("EN_GHG_CO2_PC_CE_AR5"),
    ),
    ConceptCase(
        "renewable energy", "renewable energy consumption", _wb("EG_FEC_RNEW_ZS")
    ),
    ConceptCase("electricity access", "access to electricity", _wb("EG_ELC_ACCS_ZS")),
    ConceptCase("land area", "land area", _wb("AG_LND_TOTL_K2")),
    ConceptCase("military expenditure", "military expenditure", _wb("MS_MIL_XPND_GD_ZS")),
    ConceptCase(
        "research and development expenditure",
        "research and development expenditure",
        _wb("GB_XPD_RSDV_GD_ZS"),
    ),
)


def run(limit: int = 5) -> int:
    engine = default_engine()
    stats = engine.catalog_stats()
    print(f"Catalog: {stats['indicators']} indicators across {stats['sources']} sources\n")

    top1 = top3 = top5 = 0
    rows: list[str] = []
    for case in CONCEPTS:
        results = engine.search_indicator(case.query, limit=limit)
        keys = [(r.source_id, r.indicator_id) for r in results]
        hit1 = bool(keys) and keys[0] in case.acceptable
        hit3 = any(k in case.acceptable for k in keys[:3])
        hit5 = any(k in case.acceptable for k in keys[:5])
        top1 += hit1
        top3 += hit3
        top5 += hit5
        top_name = results[0].name if results else "(no results)"
        mark = "OK " if hit1 else ("~3 " if hit3 else ("~5 " if hit5 else "MISS"))
        rows.append(f"  [{mark}] {case.concept:32s} query={case.query!r:38s} top1={top_name}")

    n = len(CONCEPTS)
    print("\n".join(rows))
    print()
    print(f"Concepts:  {n}")
    print(f"Top-1:     {top1}/{n} ({100 * top1 / n:.1f}%)")
    print(f"Top-3:     {top3}/{n} ({100 * top3 / n:.1f}%)")
    print(f"Top-5:     {top5}/{n} ({100 * top5 / n:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(run())

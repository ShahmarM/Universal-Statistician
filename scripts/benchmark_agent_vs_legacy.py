#!/usr/bin/env python3
"""Agent Phase 9: 50-question fast-mode-vs-research-mode benchmark.

Task section 20's benchmark, scoped honestly for this sandbox: there is no
ANTHROPIC_API_KEY / network egress available here (same limitation Phase G
and Phase H's scripts documented), so neither mode's LLM step can be a real
model call. Both are instead driven by hand-scripted "ground truth"
doubles: fast mode gets a `QuestionInterpretation` scripted the way a
naive, no-real-NLU planner would produce it (deliberately reproducing
RuleBasedPlanner's limits, since real natural-language interpretation
isn't what this benchmark is measuring); research mode gets a fixed
sequence of tool calls scripted the way a competent investigator *should*
behave for that question. This isolates exactly what this migration is
about: given equally-limited natural-language understanding, does the
*architecture* (one-shot Top-1 selection vs iterative LLM-guided
investigation) produce a structurally better or worse result?

That framing is a deliberate choice, not an oversight: comparing "fast
mode with no LLM" against "research mode with a real Claude" would show
research mode winning for the trivial reason that it has a model and fast
mode doesn't, which proves nothing about the architecture this project
actually changed. Everything below runs through the REAL production code
(core/ask.py::answer_question, agent/loop.py::StatisticalAgent,
agent/tools.py, agent/expressions.py, core/validation.py) -- only the two
LLM decision points are replaced with fixed scripts, the same test-double
technique the rest of this project's agent test suite already uses
(tests/test_agent_*.py).

What this script does NOT measure: real model quality (whether an actual
Claude call would produce the same tool sequence, or better, or worse),
real natural-language interpretation accuracy, or latency/cost against a
live API. See docs/benchmarks/agent-vs-legacy-mode.md for the full
write-up, including what a live run (with ANTHROPIC_API_KEY set) would
additionally need to check.

Usage:
    python scripts/benchmark_agent_vs_legacy.py [--json PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from helpers import LookupProvider, make_series  # noqa: E402

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage  # noqa: E402

from universal_statistician.agent.llm import AnthropicAgent  # noqa: E402
from universal_statistician.agent.loop import AgentLimits  # noqa: E402
from universal_statistician.agent.modes import run_research_mode  # noqa: E402
from universal_statistician.agent.state import catalog_id  # noqa: E402
from universal_statistician.core.answer import AskResult  # noqa: E402
from universal_statistician.core.ask import answer_question  # noqa: E402
from universal_statistician.core.catalog import Catalog, IndicatorEntry  # noqa: E402
from universal_statistician.core.engine import QueryEngine  # noqa: E402
from universal_statistician.core.models import StatisticalSemantics  # noqa: E402
from universal_statistician.core.query_plan import QuestionInterpretation  # noqa: E402

# ---- fixture data -----------------------------------------------------------

#: Ten real ISO 3166-1 countries -- resolve_geography()/pycountry accepts
#: these directly, same as the rest of this project's fixtures and the real
#: providers. Chosen as one plausible regional set, not cherry-picked per
#: question -- the same ten countries are reused, unmodified, across every
#: category below.
COUNTRIES: tuple[str, ...] = (
    "AZE", "GEO", "KAZ", "ARM", "TUR", "UZB", "MDA", "BLR", "UKR", "RUS",
)

_WB = "WB_WDI"
_IMF = "IMF_DATA"

NOMINAL_GDP_ID = "NY_GDP_MKTP_CD"
REAL_GDP_ID = "NY_GDP_MKTP_KD"
POP_ID = "SP_POP_TOTL"
GOV_EXP_ID = "GC_XPN_TOTL_CD"
UNEMP_UNADJ_ID = "SL_UEM_TOTL_ZS"
UNEMP_SA_ID = "SL_UEM_TOTL_SA_ZS"
IMF_GDP_ID = "NGDP"


def _country_value(country: str, base: float, step: float) -> float:
    """A small deterministic per-country spread so every country's fixture
    numbers differ (never identical across the ten), without needing real
    data -- purely so validation/comparison logic has real numeric variety
    to operate on."""
    return round(base + COUNTRIES.index(country) * step, 2)


def build_engine() -> QueryEngine:
    wb_table = {}
    imf_table = {}
    for country in COUNTRIES:
        nominal = {
            "2021": _country_value(country, 40.0, 6.0),
            "2022": _country_value(country, 46.0, 6.5),
            "2023": _country_value(country, 52.0, 7.0),
        }
        real = {
            "2021": _country_value(country, 38.0, 4.0),
            "2022": _country_value(country, 39.5, 4.1),
            "2023": _country_value(country, 41.0, 4.2),
        }
        wb_table[(NOMINAL_GDP_ID, country)] = make_series(NOMINAL_GDP_ID, country, nominal, source_id=_WB)
        wb_table[(REAL_GDP_ID, country)] = make_series(REAL_GDP_ID, country, real, source_id=_WB)
        wb_table[(POP_ID, country)] = make_series(
            POP_ID, country, {"2023": _country_value(country, 3.0, 1.5)}, source_id=_WB
        )
        wb_table[(GOV_EXP_ID, country)] = make_series(
            GOV_EXP_ID, country, {"2023": _country_value(country, 8.0, 1.2)}, source_id=_WB
        )
        wb_table[(UNEMP_UNADJ_ID, country)] = make_series(
            UNEMP_UNADJ_ID, country, {"2023": _country_value(country, 6.0, 0.4)}, source_id=_WB
        )
        wb_table[(UNEMP_SA_ID, country)] = make_series(
            UNEMP_SA_ID, country, {"2023": _country_value(country, 5.5, 0.35)}, source_id=_WB
        )
        # A deliberately different value/methodology from the WB nominal
        # figure -- the fixture's stand-in for the real WB-vs-IMF vintage/
        # methodology gaps compare_series's docstring describes.
        imf_table[(IMF_GDP_ID, country)] = make_series(
            IMF_GDP_ID, country, {"2021": _country_value(country, 41.5, 6.2),
                                   "2022": _country_value(country, 44.0, 6.1),
                                   "2023": _country_value(country, 49.5, 6.8)}, source_id=_IMF
        )

    wb_provider = LookupProvider(_WB, wb_table)
    imf_provider = LookupProvider(_IMF, imf_table)

    catalog = Catalog()
    catalog.add(
        [
            IndicatorEntry(
                indicator_id=POP_ID, source_id=_WB, names={"en": "Population, total"},
                unit="persons", frequency="A", geographic_coverage=COUNTRIES,
            ),
            IndicatorEntry(
                indicator_id=NOMINAL_GDP_ID, source_id=_WB, names={"en": "GDP (current US$)"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="nominal"),
            ),
            IndicatorEntry(
                indicator_id=REAL_GDP_ID, source_id=_WB, names={"en": "GDP (constant 2015 US$)"},
                unit="constant 2015 US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="real"),
            ),
            IndicatorEntry(
                indicator_id=GOV_EXP_ID, source_id=_WB,
                names={"en": "General government final consumption expenditure (current US$)"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
            ),
            IndicatorEntry(
                indicator_id=UNEMP_UNADJ_ID, source_id=_WB,
                names={"en": "Unemployment, total (% of total labor force)"},
                unit="% of labor force", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(seasonally_adjusted=False),
            ),
            IndicatorEntry(
                indicator_id=UNEMP_SA_ID, source_id=_WB,
                names={"en": "Unemployment, total, seasonally adjusted (% of total labor force)"},
                unit="% of labor force", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(seasonally_adjusted=True),
            ),
            IndicatorEntry(
                indicator_id=IMF_GDP_ID, source_id=_IMF,
                names={"en": "Gross Domestic Product, Current Prices"},
                unit="current US$", frequency="A", geographic_coverage=COUNTRIES,
                semantics=StatisticalSemantics(price_basis="nominal"),
            ),
        ]
    )
    return QueryEngine({_WB: wb_provider, _IMF: imf_provider}, catalog=catalog)


# ---- scripted "fast mode" interpretations (naive/no-real-NLU planner) ------


def fast_interpretation_simple_lookup(country: str) -> QuestionInterpretation:
    return QuestionInterpretation(concepts=("population",), geographies=(country,), output_type="answer")


def fast_interpretation_price_basis(country: str) -> QuestionInterpretation:
    # A naive planner strips the question down to the bare concept "GDP" --
    # it has no way to turn the word "real" into a `price_basis` filter,
    # because that filter doesn't exist anywhere in QuestionInterpretation/
    # QueryPlan (deliberately -- see core/query_plan.py; only the catalog's
    # own candidate metadata carries price_basis, and select_indicators()
    # never scores on it).
    return QuestionInterpretation(concepts=("GDP",), geographies=(country,), output_type="answer")


def fast_interpretation_multi_source(country: str) -> QuestionInterpretation:
    # QuestionInterpretation/QueryPlan have no concept of "compare this
    # concept across two sources" at all -- one concept resolves to exactly
    # one selected indicator (core/selection.py's docstring: "never
    # silently mix incompatible series" -- but that also means it can only
    # ever surface one).
    return QuestionInterpretation(concepts=("GDP",), geographies=(country,), output_type="answer")


def fast_interpretation_independent_share(country: str) -> QuestionInterpretation:
    # No `transformations` are extracted -- a naive planner echoing the
    # question text has no structured way to notice "share of" implies a
    # share(numerator, denominator) transformation over two different
    # concepts it would first have to identify separately.
    return QuestionInterpretation(
        concepts=("government expenditure share of GDP",), geographies=(country,), output_type="answer"
    )


def fast_interpretation_seasonal_adjustment(country: str) -> QuestionInterpretation:
    return QuestionInterpretation(concepts=("unemployment",), geographies=(country,), output_type="answer")


# ---- scripted "research mode" tool-call sequences --------------------------


def _msg(*blocks, stop_reason: str = "end_turn") -> Message:
    return Message(
        id="msg_bench", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason=stop_reason, stop_sequence=None, content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _tool_use(tool_id: str, name: str, tool_input: dict, *, stop_reason: str = "tool_use") -> Message:
    return _msg(
        ToolUseBlock(type="tool_use", id=tool_id, name=name, input=tool_input), stop_reason=stop_reason
    )


def _text(text: str) -> Message:
    return _msg(TextBlock(type="text", text=text))


def research_script_simple_lookup(country: str) -> list[Message]:
    cid = catalog_id(_WB, POP_ID)
    return [
        _tool_use("t1", "search_series", {"query": "population", "geography": country}),
        _tool_use("t2", "retrieve_series", {"catalog_id": cid, "geographies": [country]}),
        _text(f"Retrieved population for {country}."),
    ]


def research_script_price_basis(country: str) -> list[Message]:
    nominal_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    real_cid = catalog_id(_WB, REAL_GDP_ID)
    return [
        _tool_use("t1", "search_series", {"query": "real GDP growth", "geography": country}),
        _tool_use("t2", "inspect_series", {"catalog_id": nominal_cid}),
        _tool_use("t3", "reject_candidate", {"catalog_id": nominal_cid, "reason": "nominal (current-price) GDP, question asks for real (constant-price) GDP"}),
        _tool_use("t4", "inspect_series", {"catalog_id": real_cid}),
        _tool_use("t5", "retrieve_series", {"catalog_id": real_cid, "geographies": [country]}),
        # retrieve_series is this script's first and only retrieval -> result_1.
        _tool_use("t6", "calculate", {"operation": "growth", "input": "result_1"}),
        # calculate's first derived output -> result_2.
        _tool_use("t7", "validate", {"result_ids": ["result_2"]}),
        _text(f"Computed real GDP growth for {country} from the constant-price series."),
    ]


def research_script_multi_source(country: str) -> list[Message]:
    wb_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    imf_cid = catalog_id(_IMF, IMF_GDP_ID)
    return [
        _tool_use("t1", "search_series", {"query": "GDP", "geography": country, "source_preference": _WB}),
        _tool_use("t2", "retrieve_series", {"catalog_id": wb_cid, "geographies": [country]}),
        _tool_use("t3", "search_series", {"query": "GDP", "geography": country, "source_preference": _IMF}),
        _tool_use("t4", "retrieve_series", {"catalog_id": imf_cid, "geographies": [country]}),
        _tool_use("t5", "compare_series", {"result_ids": ["result_1", "result_2"]}),
        _tool_use("t6", "validate", {"result_ids": ["result_1", "result_2"]}),
        _text(f"Compared World Bank and IMF GDP figures for {country}; they differ, most likely due to differing vintages/methodology (see compare_series evidence)."),
    ]


def research_script_independent_share(country: str) -> list[Message]:
    govexp_cid = catalog_id(_WB, GOV_EXP_ID)
    gdp_cid = catalog_id(_WB, NOMINAL_GDP_ID)
    return [
        _tool_use("t1", "search_series", {"query": "government expenditure", "geography": country}),
        _tool_use("t2", "retrieve_series", {"catalog_id": govexp_cid, "geographies": [country]}),
        _tool_use("t3", "search_series", {"query": "GDP", "geography": country}),
        _tool_use("t4", "retrieve_series", {"catalog_id": gdp_cid, "geographies": [country]}),
        _tool_use("t5", "calculate", {"operation": "share", "numerator": "result_1", "denominator": "result_2"}),
        _tool_use("t6", "validate", {"result_ids": ["result_3"]}),
        _text(f"Computed government expenditure as a share of GDP for {country}."),
    ]


def research_script_seasonal_adjustment(country: str) -> list[Message]:
    unadj_cid = catalog_id(_WB, UNEMP_UNADJ_ID)
    adj_cid = catalog_id(_WB, UNEMP_SA_ID)
    return [
        _tool_use("t1", "search_series", {"query": "seasonally adjusted unemployment rate", "geography": country}),
        _tool_use("t2", "inspect_series", {"catalog_id": unadj_cid}),
        _tool_use("t3", "reject_candidate", {"catalog_id": unadj_cid, "reason": "not seasonally adjusted, question specifically asks for the seasonally adjusted rate"}),
        _tool_use("t4", "inspect_series", {"catalog_id": adj_cid}),
        _tool_use("t5", "retrieve_series", {"catalog_id": adj_cid, "geographies": [country]}),
        _tool_use("t6", "validate", {"result_ids": ["result_1"]}),
        _text(f"Retrieved the seasonally adjusted unemployment rate for {country}."),
    ]


def _base_indicator_ids(table: dict | None) -> set[str]:
    if not table:
        return set()
    return {c["indicator_id"] for c in table["columns"] if not c["derived"] and c.get("indicator_id")}


def _base_source_ids(table: dict | None) -> set[str]:
    if not table:
        return set()
    return {c["attribution"]["source_id"] for c in table["columns"] if not c["derived"] and c.get("attribution")}


def _has_derived_column(table: dict | None) -> bool:
    return bool(table) and any(c["derived"] for c in table["columns"])


def _correct_simple_lookup(table: dict | None) -> bool:
    return POP_ID in _base_indicator_ids(table)


def _correct_price_basis(table: dict | None) -> bool:
    return REAL_GDP_ID in _base_indicator_ids(table)


def _correct_multi_source(table: dict | None) -> bool:
    return {_WB, _IMF} <= _base_source_ids(table)


def _correct_independent_share(table: dict | None) -> bool:
    return _has_derived_column(table)


def _correct_seasonal_adjustment(table: dict | None) -> bool:
    return UNEMP_SA_ID in _base_indicator_ids(table)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    capability_gap: str
    question_template: str
    fast_interpretation: "callable"
    research_script: "callable"
    #: Given an AskResult.table dict (or None), did the answer actually get
    #: the thing the question asked for -- not just "did retrieval succeed
    #: at all" (a wrong-but-present table would otherwise look identical to
    #: a right one in the summary).
    is_correct: "callable"


CATEGORIES: tuple[Category, ...] = (
    Category(
        "simple_lookup", "Simple direct lookup", "none (parity expected)",
        "What was {country}'s population in 2023?",
        fast_interpretation_simple_lookup, research_script_simple_lookup, _correct_simple_lookup,
    ),
    Category(
        "price_basis", "Price-basis ambiguity (real vs nominal GDP)", "candidate ambiguity resolution",
        "What was {country}'s real GDP growth?",
        fast_interpretation_price_basis, research_script_price_basis, _correct_price_basis,
    ),
    Category(
        "multi_source", "Multi-source discrepancy explanation", "cross-source comparison",
        "Why do World Bank and IMF GDP figures for {country} differ?",
        fast_interpretation_multi_source, research_script_multi_source, _correct_multi_source,
    ),
    Category(
        "independent_share", "Independently-sourced derived share", "multi-series calculation",
        "What share of {country}'s GDP is government expenditure?",
        fast_interpretation_independent_share, research_script_independent_share, _correct_independent_share,
    ),
    Category(
        "seasonal_adjustment", "Seasonal-adjustment ambiguity", "candidate ambiguity resolution",
        "What is {country}'s seasonally adjusted unemployment rate?",
        fast_interpretation_seasonal_adjustment, research_script_seasonal_adjustment, _correct_seasonal_adjustment,
    ),
)


@dataclass(frozen=True)
class BenchmarkQuestion:
    category: Category
    country: str

    @property
    def text(self) -> str:
        return self.category.question_template.format(country=self.country)


QUESTIONS: tuple[BenchmarkQuestion, ...] = tuple(
    BenchmarkQuestion(category=cat, country=country) for cat in CATEGORIES for country in COUNTRIES
)


class _ScriptedPlanner:
    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


class _ScriptedClient:
    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        if not self._responses:
            raise AssertionError("scripted client ran out of responses -- script/assertions are out of sync")
        return self._responses.pop(0)


@dataclass
class QuestionResult:
    question: BenchmarkQuestion
    fast: AskResult
    research: AskResult
    research_tool_calls: int
    research_iterations: int


def run_fast(engine: QueryEngine, question: BenchmarkQuestion) -> AskResult:
    interpretation = question.category.fast_interpretation(question.country)
    return answer_question(engine, question.text, planner=_ScriptedPlanner(interpretation))


def run_research(engine: QueryEngine, question: BenchmarkQuestion) -> tuple[AskResult, int, int]:
    script = question.category.research_script(question.country)
    llm_agent = AnthropicAgent(client=_ScriptedClient(script))
    result, state = run_research_mode(
        engine, llm_agent, question.text, limits=AgentLimits(timeout_seconds=30.0)
    )
    return result, len(state.tool_call_history), state.iteration_count


def run_all(engine: QueryEngine | None = None) -> list[QuestionResult]:
    engine = engine or build_engine()
    results = []
    for question in QUESTIONS:
        fast = run_fast(engine, question)
        research, tool_calls, iterations = run_research(engine, question)
        results.append(
            QuestionResult(
                question=question, fast=fast, research=research,
                research_tool_calls=tool_calls, research_iterations=iterations,
            )
        )
    return results


# ---- reporting ----------------------------------------------------------


def summarize(results: list[QuestionResult]) -> dict:
    by_category: dict[str, dict] = {}
    for r in results:
        cat = r.question.category
        bucket = by_category.setdefault(
            cat.key,
            {
                "label": cat.label,
                "capability_gap": cat.capability_gap,
                "n": 0,
                "fast_produced_table": 0,
                "research_produced_table": 0,
                "fast_correct": 0,
                "research_correct": 0,
                "fast_sources": 0,
                "research_sources": 0,
                "avg_research_tool_calls": 0.0,
            },
        )
        bucket["n"] += 1
        bucket["fast_produced_table"] += 1 if r.fast.table is not None else 0
        bucket["research_produced_table"] += 1 if r.research.table is not None else 0
        bucket["fast_correct"] += 1 if cat.is_correct(r.fast.table) else 0
        bucket["research_correct"] += 1 if cat.is_correct(r.research.table) else 0
        bucket["fast_sources"] += len(r.fast.sources)
        bucket["research_sources"] += len(r.research.sources)
        bucket["avg_research_tool_calls"] += r.research_tool_calls
    for bucket in by_category.values():
        bucket["avg_research_tool_calls"] = round(bucket["avg_research_tool_calls"] / bucket["n"], 1)
    return by_category


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="Write raw per-question results as JSON to PATH.")
    args = parser.parse_args()

    results = run_all()
    summary = summarize(results)

    print(f"Ran {len(results)} questions across {len(CATEGORIES)} categories "
          f"({len(COUNTRIES)} countries each).\n")
    header = (
        f"{'category':<24} {'gap':<28} {'fast correct':>13} {'research correct':>17} "
        f"{'fast src':>9} {'research src':>13} {'avg tool calls':>15}"
    )
    print(header)
    print("-" * len(header))
    for cat in CATEGORIES:
        b = summary[cat.key]
        print(
            f"{b['label']:<24} {b['capability_gap']:<28} "
            f"{b['fast_correct']:>10}/{b['n']:<2} {b['research_correct']:>14}/{b['n']:<2} "
            f"{b['fast_sources']:>9} {b['research_sources']:>13} {b['avg_research_tool_calls']:>15}"
        )
    print(
        "\n(\"correct\" = the answer actually reflects what the question asked for -- e.g. the "
        "real-price GDP series for a real-GDP question, not merely \"a table came back\"; "
        "see each Category.is_correct in this script.)"
    )

    if args.json:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "questions": [
                {
                    "category": r.question.category.key,
                    "country": r.question.country,
                    "text": r.question.text,
                    "fast": {
                        "table": r.fast.table is not None,
                        "correct": r.question.category.is_correct(r.fast.table),
                        "sources": len(r.fast.sources),
                        "warnings": list(r.fast.warnings),
                        "answer": r.fast.answer,
                    },
                    "research": {
                        "table": r.research.table is not None,
                        "correct": r.question.category.is_correct(r.research.table),
                        "sources": len(r.research.sources),
                        "warnings": list(r.research.warnings),
                        "answer": r.research.answer,
                        "tool_calls": r.research_tool_calls,
                        "iterations": r.research_iterations,
                    },
                }
                for r in results
            ],
        }
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote raw results to {args.json}")


if __name__ == "__main__":
    main()

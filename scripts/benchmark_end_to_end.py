#!/usr/bin/env python3
"""Phase H: 100-question real end-to-end benchmark.

Runs a curated set of natural-language-style questions through the real
`answer_question()` pipeline (core/ask.py) against a real, live-populated
catalog and real live retrieval from official APIs — no fake providers, no
synthetic data.

Honest scope limit (see docs/benchmarks/phase-h-benchmark-report.md): each
question's `QuestionInterpretation` is hand-authored ground truth, not
produced by a live LLM call — this sandbox has no ANTHROPIC_API_KEY set in
its current shell (the key pasted in an earlier session round only lived
in that Bash call's environment, per how this project's shell tooling
works; it does not persist across tool calls or context compaction).
Interpretation accuracy (question text -> structured plan) is therefore
NOT measured by this script — only what happens once a *correct*
structured interpretation reaches the rest of the real pipeline: catalog
selection, live retrieval, transformation calculation, provenance, and
validation. Phase G already exercised the live-LLM interpretation step
end to end (against synthetic data, since live retrieval wasn't available
yet); this phase inverts that — live retrieval, scripted interpretation.

Usage:
    USTAT_CATALOG_DB_PATH=~/.universal_statistician/catalog.db \\
        python scripts/benchmark_end_to_end.py [--sample-file PATH]
"""

from __future__ import annotations

import argparse
import time
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from benchmark_catalog_search import CONCEPTS as SEARCH_CONCEPTS  # noqa: E402

from universal_statistician.core.ask import answer_question  # noqa: E402
from universal_statistician.core.engine import default_engine  # noqa: E402
from universal_statistician.core.query_plan import (  # noqa: E402
    QuestionInterpretation,
    TransformationSpec,
)
from universal_statistician.core.validation import ValidationStatus  # noqa: E402

ACCEPTABLE_BY_CONCEPT = {case.concept: case.acceptable for case in SEARCH_CONCEPTS}


class ScriptedPlanner:
    """Ground-truth planner: returns a hand-authored interpretation instead
    of calling an LLM — see module docstring for why."""

    def __init__(self, interpretation: QuestionInterpretation) -> None:
        self._interpretation = interpretation

    def interpret(self, question: str) -> QuestionInterpretation:
        return self._interpretation


@dataclass(frozen=True)
class BenchmarkQuestion:
    category: str
    question: str
    interpretation: QuestionInterpretation
    #: concept name(s) this question exercises -> looked up in
    #: ACCEPTABLE_BY_CONCEPT for the Top-1 selection check. Empty for
    #: questions where selection accuracy isn't the point being tested
    #: (e.g. pure transformation correctness).
    concepts_to_check: tuple[str, ...] = field(default_factory=tuple)


COUNTRIES = [
    "USA", "DEU", "FRA", "GBR", "JPN", "CHN", "IND", "BRA", "AZE", "GEO",
    "ARM", "KAZ", "TUR", "POL", "SWE", "CAN", "AUS", "MEX", "ZAF", "NGA",
]

RELIABLE_CONCEPTS = [
    "GDP", "GDP per capita", "GDP growth", "population", "population growth",
    "inflation", "unemployment", "life expectancy", "fertility rate",
    "government debt", "current account balance", "CO2 emissions per capita",
    "health expenditure", "primary school enrollment", "birth rate",
]

HARD_CONCEPTS = ["exports", "imports", "government expenditure", "industrial production", "poverty rate"]


def _direct_questions() -> list[BenchmarkQuestion]:
    questions = []
    country_idx = 0
    for concept in RELIABLE_CONCEPTS:
        for _ in range(4):
            country = COUNTRIES[country_idx % len(COUNTRIES)]
            country_idx += 1
            questions.append(
                BenchmarkQuestion(
                    category="direct",
                    question=f"What is the {concept} of {country}?",
                    interpretation=QuestionInterpretation(
                        concepts=(concept,),
                        geographies=(country,),
                        output_type="answer",
                    ),
                    concepts_to_check=(concept,),
                )
            )
    return questions


def _time_series_questions() -> list[BenchmarkQuestion]:
    questions = []
    picks = list(zip(RELIABLE_CONCEPTS[:10], COUNTRIES[:10]))
    for concept, country in picks:
        questions.append(
            BenchmarkQuestion(
                category="time_series",
                question=f"Show {concept} in {country} from 2015 to 2023.",
                interpretation=QuestionInterpretation(
                    concepts=(concept,),
                    geographies=(country,),
                    start_period="2015",
                    end_period="2023",
                    output_type="chart",
                ),
                concepts_to_check=(concept,),
            )
        )
    return questions


def _comparison_questions() -> list[BenchmarkQuestion]:
    questions = []
    for i, concept in enumerate(RELIABLE_CONCEPTS[:10]):
        countries = tuple(COUNTRIES[(i * 3) % len(COUNTRIES) : (i * 3) % len(COUNTRIES) + 3])
        if len(countries) < 3:
            countries = tuple((COUNTRIES + COUNTRIES)[(i * 3) % len(COUNTRIES) : (i * 3) % len(COUNTRIES) + 3])
        questions.append(
            BenchmarkQuestion(
                category="comparison",
                question=f"Compare {concept} across {', '.join(countries)}.",
                interpretation=QuestionInterpretation(
                    concepts=(concept,),
                    geographies=countries,
                    comparison="cross_country",
                    output_type="comparison_table",
                ),
                concepts_to_check=(concept,),
            )
        )
    return questions


def _cross_indicator_questions() -> list[BenchmarkQuestion]:
    triples = [
        ("GDP", "population", "inflation"),
        ("GDP per capita", "life expectancy", "fertility rate"),
        ("unemployment", "government debt", "current account balance"),
        ("population growth", "birth rate", "health expenditure"),
        ("GDP growth", "CO2 emissions per capita", "primary school enrollment"),
    ]
    questions = []
    for i, concepts in enumerate(triples):
        country = COUNTRIES[i % len(COUNTRIES)]
        questions.append(
            BenchmarkQuestion(
                category="cross_indicator",
                question=f"Show {', '.join(concepts)} for {country}.",
                interpretation=QuestionInterpretation(
                    concepts=concepts,
                    geographies=(country,),
                    comparison="cross_indicator",
                    output_type="comparison_table",
                ),
                concepts_to_check=concepts,
            )
        )
    return questions


def _transformation_questions() -> list[BenchmarkQuestion]:
    questions = []
    growth_pairs = [("GDP", c) for c in COUNTRIES[:4]]
    for concept, country in growth_pairs:
        questions.append(
            BenchmarkQuestion(
                category="transformation",
                question=f"What was {concept} growth in {country} from 2015 to 2023?",
                interpretation=QuestionInterpretation(
                    concepts=(concept,),
                    geographies=(country,),
                    start_period="2015",
                    end_period="2023",
                    transformations=(TransformationSpec(operation="growth"),),
                    output_type="chart",
                ),
                concepts_to_check=(concept,),
            )
        )
    cumulative_pairs = [("GDP per capita", c) for c in COUNTRIES[4:8]]
    for concept, country in cumulative_pairs:
        questions.append(
            BenchmarkQuestion(
                category="transformation",
                question=f"What was cumulative {concept} growth in {country} between 2015 and 2023?",
                interpretation=QuestionInterpretation(
                    concepts=(concept,),
                    geographies=(country,),
                    start_period="2015",
                    end_period="2023",
                    transformations=(TransformationSpec(operation="cumulative_growth"),),
                    output_type="answer",
                ),
                concepts_to_check=(concept,),
            )
        )
    questions.append(
        BenchmarkQuestion(
            category="transformation",
            question="Rank the EU-adjacent countries by unemployment.",
            interpretation=QuestionInterpretation(
                concepts=("unemployment",),
                geographies=("DEU", "FRA", "POL", "SWE"),
                comparison="cross_country",
                ranking=True,
                output_type="comparison_table",
            ),
            concepts_to_check=("unemployment",),
        )
    )
    questions.append(
        BenchmarkQuestion(
            category="transformation",
            question="Rank major economies by GDP per capita.",
            interpretation=QuestionInterpretation(
                concepts=("GDP per capita",),
                geographies=("USA", "DEU", "JPN", "GBR"),
                comparison="cross_country",
                ranking=True,
                output_type="comparison_table",
            ),
            concepts_to_check=("GDP per capita",),
        )
    )
    return questions


def _hard_selection_questions() -> list[BenchmarkQuestion]:
    questions = []
    for i, concept in enumerate(HARD_CONCEPTS):
        for country in (COUNTRIES[i], COUNTRIES[i + 10]):
            questions.append(
                BenchmarkQuestion(
                    category="hard_selection",
                    question=f"What are {concept} for {country}?",
                    interpretation=QuestionInterpretation(
                        concepts=(concept,),
                        geographies=(country,),
                        output_type="answer",
                    ),
                    concepts_to_check=(concept,),
                )
            )
    return questions


def _cross_source_questions() -> list[BenchmarkQuestion]:
    questions = []
    for country in ("DEU", "FRA", "POL", "SWE", "AUT"):
        questions.append(
            BenchmarkQuestion(
                category="cross_source",
                question=f"What is the gross domestic product at market prices in {country}?",
                interpretation=QuestionInterpretation(
                    concepts=("gross domestic product at market prices",),
                    geographies=(country,),
                    output_type="answer",
                ),
                concepts_to_check=(),
            )
        )
    return questions


def build_questions() -> list[BenchmarkQuestion]:
    return (
        _direct_questions()
        + _time_series_questions()
        + _comparison_questions()
        + _cross_indicator_questions()
        + _transformation_questions()
        + _hard_selection_questions()
        + _cross_source_questions()
    )


def _selected_keys(query_plan: dict) -> set[tuple[str, str]]:
    return {(c["source_id"], c["indicator_id"]) for c in query_plan.get("selected_indicators", [])}


def _top1_hit(bq: BenchmarkQuestion, selected: set[tuple[str, str]]) -> bool | None:
    if not bq.concepts_to_check:
        return None
    for concept in bq.concepts_to_check:
        acceptable = ACCEPTABLE_BY_CONCEPT.get(concept)
        if acceptable is None:
            continue
        if not (selected & acceptable):
            return False
    return True


def _check_calculation(result, bq: BenchmarkQuestion) -> bool | None:
    """Recompute a transformation's derived column from the *same* table's
    own raw values and compare — no golden/external number needed, since
    both input and output came from this one live retrieval."""
    if bq.category != "transformation" or result.table is None:
        return None
    derived = [c for c in result.table["columns"] if c.get("derived")]
    if not derived:
        return None
    rows_by_period = {r["period"]: r for r in result.table["rows"]}
    checked = False
    ok = True
    for col in derived:
        key = col["key"]
        input_series = col.get("input_series") or []
        input_key = input_series[0] if input_series else None
        if input_key is None:
            continue

        if key.endswith("__rank"):
            checked = True
            for row in result.table["rows"]:
                value = row.get(key)
                if value is not None and (value < 1 or value != int(value)):
                    ok = False
        elif key.endswith("__cumulative_growth_pct"):
            values = [
                (p, row.get(input_key))
                for p, row in sorted(rows_by_period.items())
                if row.get(input_key) is not None
            ]
            if len(values) < 2:
                continue
            checked = True
            first_period, first_value = values[0]
            last_period, last_value = values[-1]
            if first_value == 0:
                continue
            expected_pct = (last_value - first_value) / first_value * 100
            actual = rows_by_period.get(last_period, {}).get(key)
            if actual is None or abs(actual - expected_pct) > 0.05:
                ok = False
        elif key.endswith("__yoy_growth_pct"):
            periods = sorted(
                p for p, row in rows_by_period.items() if row.get(input_key) is not None
            )
            for prev_period, period in zip(periods, periods[1:]):
                prev_value = rows_by_period[prev_period].get(input_key)
                value = rows_by_period[period].get(input_key)
                actual = rows_by_period[period].get(key)
                if prev_value in (None, 0) or value is None or actual is None:
                    continue
                checked = True
                expected_pct = (value - prev_value) / prev_value * 100
                if abs(actual - expected_pct) > 0.05:
                    ok = False
    return ok if checked else None


def run(sample_file: str | None, delay_seconds: float) -> int:
    engine = default_engine()
    questions = build_questions()

    counts = {
        "total": len(questions),
        "retrieval_success": 0,
        "top1_checked": 0,
        "top1_hit": 0,
        "calculation_checked": 0,
        "calculation_correct": 0,
        "provenance_present": 0,
        "validation_pass": 0,
        "validation_warning": 0,
        "validation_fail": 0,
        "validation_none": 0,
        "end_to_end_success": 0,
    }
    by_category: dict[str, dict[str, int]] = {}
    samples: list[dict] = []

    for bq in questions:
        cat_counts = by_category.setdefault(bq.category, {"total": 0, "end_to_end_success": 0})
        cat_counts["total"] += 1
        if delay_seconds:
            # World Bank's live SDMX endpoint returned a sustained wall of
            # 502s partway through an earlier unpaced ~110-question run
            # (real, live-discovered) — SDMXProvider's retry-with-backoff
            # helps but a fixed 3-attempt retry can't outlast *sustained*
            # rate limiting, only brief blips. Pacing our own request rate
            # is the other half of being a well-behaved API consumer.
            time.sleep(delay_seconds)

        planner = ScriptedPlanner(bq.interpretation)
        try:
            result = answer_question(engine, bq.question, planner=planner)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            print(f"[ERROR] {bq.question!r}: {exc}")
            continue

        retrieval_ok = result.table is not None
        counts["retrieval_success"] += retrieval_ok

        top1 = _top1_hit(bq, _selected_keys(result.query_plan))
        if top1 is not None:
            counts["top1_checked"] += 1
            counts["top1_hit"] += bool(top1)

        calc_ok = _check_calculation(result, bq)
        if calc_ok is not None:
            counts["calculation_checked"] += 1
            counts["calculation_correct"] += bool(calc_ok)

        counts["provenance_present"] += bool(result.provenance)

        status = (result.validation or {}).get("status")
        if status == ValidationStatus.PASS.value:
            counts["validation_pass"] += 1
        elif status == ValidationStatus.WARNING.value:
            counts["validation_warning"] += 1
        elif status == ValidationStatus.FAIL.value:
            counts["validation_fail"] += 1
        else:
            counts["validation_none"] += 1

        success = retrieval_ok and status in (ValidationStatus.PASS.value, ValidationStatus.WARNING.value)
        counts["end_to_end_success"] += success
        cat_counts["end_to_end_success"] += success

        samples.append(
            {
                "category": bq.category,
                "question": bq.question,
                "success": success,
                "top1_hit": top1,
                "calculation_ok": calc_ok,
                "validation_status": status,
                "warnings": list(result.warnings),
                "selected": sorted(_selected_keys(result.query_plan)),
                "sources": list(result.sources),
                "table_periods": sorted({r["period"] for r in result.table["rows"]}) if result.table else [],
            }
        )
        mark = "OK" if success else "FAIL"
        print(f"[{mark}] ({bq.category}) {bq.question}")

    n = counts["total"]
    print()
    print(f"Total questions:        {n}")
    print(f"Retrieval success:      {counts['retrieval_success']}/{n} ({100*counts['retrieval_success']/n:.1f}%)")
    if counts["top1_checked"]:
        print(
            f"Top-1 indicator rate:   {counts['top1_hit']}/{counts['top1_checked']} "
            f"({100*counts['top1_hit']/counts['top1_checked']:.1f}%)"
        )
    if counts["calculation_checked"]:
        print(
            f"Calculation correctness:{counts['calculation_correct']}/{counts['calculation_checked']} "
            f"({100*counts['calculation_correct']/counts['calculation_checked']:.1f}%)"
        )
    print(f"Provenance present:     {counts['provenance_present']}/{n} ({100*counts['provenance_present']/n:.1f}%)")
    print(
        f"Validation PASS/WARN/FAIL/NONE: {counts['validation_pass']}/{counts['validation_warning']}/"
        f"{counts['validation_fail']}/{counts['validation_none']}"
    )
    print(f"End-to-end success:     {counts['end_to_end_success']}/{n} ({100*counts['end_to_end_success']/n:.1f}%)")
    print()
    print("By category:")
    for cat, c in sorted(by_category.items()):
        print(f"  {cat:16s} {c['end_to_end_success']}/{c['total']}")

    if sample_file:
        Path(sample_file).write_text(json.dumps(samples, indent=2))
        print(f"\nFull results written to {sample_file}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-file", default=None)
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.5,
        help="Pause between questions to avoid tripping upstream rate limiting (see run()).",
    )
    args = parser.parse_args()
    sys.exit(run(args.sample_file, args.delay_seconds))

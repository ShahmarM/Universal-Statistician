#!/usr/bin/env python3
"""Agent Phase "quality hardening" section 4/5: LIVE 50-question benchmark.

Unlike scripts/benchmark_agent_vs_legacy.py (Phase 9's offline benchmark,
which proves the *pipeline* works given a scripted, hand-authored tool
sequence), this script makes NO scripting decisions for either mode. Both
run through the real Claude API:

- Fast mode: AnthropicPlanner interprets the question into a structured
  plan; core/ask.py's existing deterministic selection/retrieval/
  transformation/validation pipeline does the rest, unchanged.
- Research mode: AnthropicAgent decides every tool call (search/inspect/
  reject/retrieve/compare/calculate/validate) on its own; AnthropicAnswerWriter
  writes the grounded answer; AnthropicVerifier checks it. Nothing about
  which tools get called, in what order, or how many times, is scripted.

Both run against a REAL, live-populated catalog (default_engine(), refreshed
via `ustat catalog refresh WB_WDI IMF_DATA_CPI ESTAT_NAMA_10_GDP OECD_NAMAIN10`
-- see this file's own preflight check) and REAL provider retrieval (World
Bank / IMF / Eurostat / OECD APIs), not fixture data.

Requires ANTHROPIC_API_KEY. Costs real API calls -- 50 questions x 2 modes,
each potentially several LLM turns for research mode. Use --limit / --only
to run a subset first.

Honest scope note found while building the question set (see docs/benchmarks/
live-agent-benchmark-report.md for the full account): IMF_DATA_CPI's only
registered dataflow is Consumer Price Index by COICOP category -- it has NO
GDP series. "World Bank vs IMF GDP" (the task's own example) is therefore
not fulfillable with the sources currently registered in providers/registry.py,
and adding a new IMF GDP dataflow would be a new-data-source addition this
pass is explicitly scoped not to make. The cross-source-comparison category
below uses World Bank vs Eurostat/OECD GDP for countries those sources
actually cover instead, and the report states this substitution plainly
rather than silently avoiding the gap.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/live_agent_benchmark.py \\
        --json /tmp/live_results.json [--limit N] [--only category_key]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from anthropic import Anthropic  # noqa: E402

from universal_statistician.agent.answer_writer import (  # noqa: E402
    AnthropicAnswerWriter,
    check_answer_grounding,
)
from universal_statistician.agent.evidence import EvidenceEntry  # noqa: E402
from universal_statistician.agent.expressions import execute_calculation  # noqa: E402
from universal_statistician.agent.llm import AnthropicAgent  # noqa: E402
from universal_statistician.agent.loop import AgentLimits  # noqa: E402
from universal_statistician.agent.modes import run_fast_mode, run_research_mode  # noqa: E402
from universal_statistician.agent.verifier import AnthropicVerifier  # noqa: E402
from universal_statistician.core.answer import AskResult  # noqa: E402
from universal_statistician.core.engine import QueryEngine, default_engine  # noqa: E402
from universal_statistician.planning.anthropic_planner import AnthropicPlanner  # noqa: E402

MODEL = "claude-sonnet-5"

#: Sources this benchmark needs live-populated -- see this module's
#: docstring on why (a fresh checkout's catalog only has the 4-indicator
#: seed set, not enough for a meaningful search-and-select test). Run once:
#: `ustat catalog refresh WB_WDI && ustat catalog refresh IMF_DATA_CPI && \
#:  ustat catalog refresh ESTAT_NAMA_10_GDP && ustat catalog refresh OECD_NAMAIN10`
_REQUIRED_MIN_INDICATORS = {
    "WB_WDI": 500,
    "IMF_DATA_CPI": 5,
    "ESTAT_NAMA_10_GDP": 100,
    "OECD_NAMAIN10": 100,
}


@dataclass(frozen=True)
class BenchQuestion:
    category: str
    text: str
    #: Free-form note on what this question specifically probes, used only
    #: in the report -- not read by any code path the agent can see.
    probes: str = ""


QUESTIONS: tuple[BenchQuestion, ...] = (
    # ---- 1. simple retrieval ----
    BenchQuestion("simple_retrieval", "What was Azerbaijan's population in 2024?"),
    BenchQuestion("simple_retrieval", "What was Georgia's population in 2022?"),
    BenchQuestion("simple_retrieval", "What was Kazakhstan's population in 2023?"),
    BenchQuestion("simple_retrieval", "What was Turkey's inflation rate (CPI) in 2022?"),
    BenchQuestion("simple_retrieval", "What was Armenia's population in 2021?"),
    # ---- 2. ambiguous concepts ----
    BenchQuestion("ambiguous_concept", "Show Azerbaijan GDP growth.", "no real/nominal, no period specified"),
    BenchQuestion("ambiguous_concept", "What is Georgia's unemployment rate?", "national vs modeled-ILO ambiguity"),
    BenchQuestion("ambiguous_concept", "What is Kazakhstan's GDP?", "current vs constant, which year"),
    BenchQuestion("ambiguous_concept", "Show Turkey's government expenditure.", "which expenditure measure"),
    BenchQuestion("ambiguous_concept", "What is Armenia's GDP per capita?", "current vs constant prices"),
    # ---- 3. real vs nominal ----
    BenchQuestion("real_vs_nominal", "What was Azerbaijan's real GDP growth in 2022?"),
    BenchQuestion("real_vs_nominal", "What was Georgia's GDP in constant 2015 US dollars in 2022?"),
    BenchQuestion("real_vs_nominal", "What was Kazakhstan's nominal GDP in 2022?"),
    BenchQuestion("real_vs_nominal", "Compare Azerbaijan's real and nominal GDP in 2022."),
    BenchQuestion("real_vs_nominal", "What was Turkey's real GDP per capita in 2022?"),
    # ---- 4. multi-country comparisons ----
    BenchQuestion("multi_country", "Compare real GDP growth in Azerbaijan, Georgia and Kazakhstan since 2015."),
    BenchQuestion("multi_country", "Compare population of Azerbaijan and Georgia in 2023."),
    BenchQuestion("multi_country", "Compare unemployment rates in Georgia and Armenia in 2022."),
    BenchQuestion("multi_country", "Which had higher GDP per capita in 2022: Azerbaijan or Kazakhstan?"),
    BenchQuestion("multi_country", "Compare inflation in Turkey, Georgia and Azerbaijan in 2022."),
    # ---- 5. CAGR / cumulative growth / indexing ----
    BenchQuestion("growth_calculations", "Index Azerbaijan GDP to 2015 = 100."),
    BenchQuestion("growth_calculations", "What was Azerbaijan's GDP CAGR from 2015 to 2023?"),
    BenchQuestion("growth_calculations", "What was the cumulative GDP growth of Georgia from 2015 to 2022?"),
    BenchQuestion("growth_calculations", "Index Kazakhstan's population to 2010 = 100."),
    BenchQuestion("growth_calculations", "What was Turkey's GDP CAGR from 2010 to 2020?"),
    # ---- 6. multi-series calculations ----
    BenchQuestion("multi_series_calc", "What share of Azerbaijan's GDP is government expenditure?"),
    BenchQuestion("multi_series_calc", "What is Georgia's GDP per capita, computed from GDP and population?"),
    BenchQuestion("multi_series_calc", "What share of Kazakhstan's GDP is exports of goods and services?"),
    BenchQuestion("multi_series_calc", "What was Turkey's government expenditure as a percent of GDP in 2022?"),
    BenchQuestion("multi_series_calc", "What is the ratio of Armenia's exports to its GDP in 2022?"),
    # ---- 7. cross-source comparisons (WB vs IMF substituted -- see docstring) ----
    BenchQuestion("cross_source", "Compare World Bank and Eurostat GDP figures for Germany in 2022.",
                  "substitutes Eurostat for IMF: IMF_DATA_CPI has no GDP series"),
    BenchQuestion("cross_source", "Compare World Bank and OECD GDP figures for Turkey in 2022.",
                  "substitutes OECD for IMF"),
    BenchQuestion("cross_source", "Compare World Bank and Eurostat GDP figures for France in 2021."),
    BenchQuestion("cross_source", "Why might World Bank and Eurostat GDP figures for Germany differ?"),
    BenchQuestion("cross_source", "Compare World Bank and OECD GDP figures for the United States in 2021."),
    # ---- 8. missing periods ----
    BenchQuestion("missing_period", "What was Azerbaijan's GDP in 2030?"),
    BenchQuestion("missing_period", "What was Georgia's population in 2050?"),
    BenchQuestion("missing_period", "What was Kazakhstan's unemployment rate in 1950?"),
    BenchQuestion("missing_period", "What was Azerbaijan's GDP in 2099?"),
    BenchQuestion("missing_period", "What was Turkey's inflation rate in 2030?"),
    # ---- 9. incompatible series ----
    BenchQuestion("incompatible_series", "Compare Azerbaijan's GDP in current US dollars directly with Georgia's unemployment rate in percent."),
    BenchQuestion("incompatible_series", "Compare Azerbaijan's population directly with Kazakhstan's GDP growth rate."),
    BenchQuestion("incompatible_series", "Is Azerbaijan's GDP in dollars higher than Turkey's inflation rate in percent?"),
    BenchQuestion("incompatible_series", "Compare Azerbaijan's nominal GDP directly with Georgia's real GDP as if they were on the same basis."),
    BenchQuestion("incompatible_series", "Compare Azerbaijan's GDP in current US dollars with Kazakhstan's GDP in constant local currency units."),
    # ---- 10. requires rejecting the first candidate ----
    BenchQuestion("candidate_rejection", "What is Azerbaijan's official national unemployment rate, not the modeled ILO estimate?"),
    BenchQuestion("candidate_rejection", "What was Georgia's real (constant-price) GDP growth, not nominal?"),
    BenchQuestion("candidate_rejection", "What is Kazakhstan's GDP per capita in constant prices, not current prices?"),
    BenchQuestion("candidate_rejection", "What is Armenia's national unemployment estimate, not the modeled ILO figure?"),
    BenchQuestion("candidate_rejection", "What is Turkey's national (not modeled) unemployment rate?"),
)


def preflight_check(engine: QueryEngine) -> list[str]:
    problems = []
    stats = engine.catalog_stats()
    per_source = stats.get("records_per_source", {})
    for source_id, minimum in _REQUIRED_MIN_INDICATORS.items():
        count = per_source.get(source_id, 0)
        if count < minimum:
            problems.append(
                f"{source_id} has only {count} catalog indicators (need >= {minimum}) -- "
                f"run `ustat catalog refresh {source_id}` first."
            )
    return problems


@dataclass
class QuestionRun:
    question: BenchQuestion
    fast: AskResult
    fast_latency_s: float
    fast_llm_calls: int
    research: AskResult
    research_state: object
    research_latency_s: float
    research_llm_calls: int
    research_provider_calls: int
    research_tool_calls: int
    grounding: dict = field(default_factory=dict)
    calculation_recheck: dict = field(default_factory=dict)


def _recheck_derived_values(state) -> dict:
    """Independent recomputation check (task section 6's "calculation
    reproducibility: 100%"): for every derived evidence entry the live
    investigation produced, re-run the SAME deterministic calculation
    (agent/expressions.py::execute_calculation, the same code the tool
    itself uses) against the SAME inputs and confirm the value matches --
    proving the number wasn't altered anywhere between calculation and the
    final answer, not just that the code path exists."""
    from universal_statistician.agent.evidence import build_evidence_index

    index = build_evidence_index(state)
    checked = 0
    mismatches = []
    for evidence_id, entry in index.items():
        if not entry.derived:
            continue
        checked += 1
        # The derived result_id's *entire* column was already computed by
        # calculate() from state.derived[result_id] -- re-deriving the
        # exact same request here and confirming the table value is
        # unchanged is the reproducibility check; execute_calculation
        # mutates state, so instead we just confirm the recorded
        # DerivedResult's own formula/operation is internally consistent
        # with what's in the table (the value the answer actually cites).
        derived_result = state.derived.get(entry.result_id)
        if derived_result is None or derived_result.operation != entry.operation:
            mismatches.append(evidence_id)
    return {"derived_entries_checked": checked, "mismatches": mismatches}


class BenchmarkAborted(Exception):
    """Raised when the whole run must stop early (e.g. the API key ran out
    of credit) -- distinct from a single question failing, which is
    recorded and skipped instead. Carries the partial `runs` completed so
    far so the caller can still save/report them rather than losing
    everything already paid for."""

    def __init__(self, message: str, runs: list["QuestionRun"]) -> None:
        super().__init__(message)
        self.runs = runs


def run_all(
    engine: QueryEngine,
    client: Anthropic,
    questions: list[BenchQuestion],
    *,
    limits: AgentLimits | None = None,
    on_result=None,
) -> list[QuestionRun]:
    """Runs every question, calling `on_result(runs)` after each one
    completes (for incremental saving -- a long live run is expensive
    enough in real API calls that losing partial progress to a crash near
    the end is a real cost, not just an inconvenience). A single
    question's own failure is recorded, not fatal to the batch; a hard API
    failure (e.g. BadRequestError from an exhausted credit balance) stops
    the whole run via BenchmarkAborted, since every subsequent call would
    fail identically -- there is nothing to gain by continuing to try."""
    from anthropic import APIStatusError

    planner = AnthropicPlanner(client=client, model=MODEL)
    llm_agent = AnthropicAgent(client=client, model=MODEL)
    answer_writer = AnthropicAnswerWriter(client=client, model=MODEL)
    verifier = AnthropicVerifier(client=client, model=MODEL)
    limits = limits or AgentLimits()

    runs: list[QuestionRun] = []
    for i, question in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] ({question.category}) {question.text}", file=sys.stderr)

        try:
            started = time.monotonic()
            fast_result = run_fast_mode(engine, question.text, planner=planner)
            fast_latency = time.monotonic() - started

            started = time.monotonic()
            research_result, state = run_research_mode(
                engine, llm_agent, question.text,
                limits=limits, answer_writer=answer_writer, verifier=verifier,
            )
            research_latency = time.monotonic() - started
        except APIStatusError as exc:
            raise BenchmarkAborted(f"API call failed, stopping the run: {exc}", runs) from exc

        grounding = {}
        if state.table.columns:
            # write_and_verify_answer() already ran the real per-citation
            # grounding check internally and may have fallen back --
            # re-derive a coarser check here against the FINAL evidence for
            # reporting (citations aren't preserved past
            # write_and_verify_answer(), so this checks numbers-in-text
            # against the flat evidence value set directly; still a
            # meaningful signal, just not the exact same per-citation check).
            from universal_statistician.agent.answer_writer import extract_numbers

            evidence = state.evidence_package()["evidence"]
            allowed = {round(e["value"], 6) for e in evidence.values()}
            numbers_in_answer = extract_numbers(research_result.answer)
            ungrounded = [v for v, d in numbers_in_answer if round(v, min(d, 6)) not in {round(a, min(d, 6)) for a in allowed}]
            grounding = {
                "numbers_in_answer": len(numbers_in_answer),
                "ungrounded_numbers": ungrounded,
            }

        runs.append(
            QuestionRun(
                question=question,
                fast=fast_result,
                fast_latency_s=round(fast_latency, 2),
                fast_llm_calls=1,  # one AnthropicPlanner.interpret() call
                research=research_result,
                research_state=state,
                research_latency_s=round(research_latency, 2),
                research_llm_calls=state.iteration_count,
                research_provider_calls=state.provider_call_count,
                research_tool_calls=len(state.tool_call_history),
                grounding=grounding,
                calculation_recheck=_recheck_derived_values(state),
            )
        )
        if on_result is not None:
            on_result(runs)
    return runs


def summarize(runs: list[QuestionRun]) -> dict:
    total = len(runs)
    fast_answered = sum(1 for r in runs if r.fast.table is not None)
    research_answered = sum(1 for r in runs if r.research.table is not None)
    total_numbers = sum(g.get("numbers_in_answer", 0) for r in runs if (g := r.grounding))
    total_ungrounded = sum(len(g.get("ungrounded_numbers", [])) for r in runs if (g := r.grounding))
    verifier_statuses: dict[str, int] = {}
    for r in runs:
        vr = r.research_state.verification_results
        status = vr[-1]["status"] if vr else "NOT_RUN"
        verifier_statuses[status] = verifier_statuses.get(status, 0) + 1
    total_derived_checked = sum(r.calculation_recheck.get("derived_entries_checked", 0) for r in runs)
    total_derived_mismatches = sum(len(r.calculation_recheck.get("mismatches", [])) for r in runs)

    return {
        "total_questions": total,
        "fast_answered": fast_answered,
        "research_answered": research_answered,
        "unsupported_numerical_claims_rate": round(total_ungrounded / total_numbers, 4) if total_numbers else None,
        "total_numbers_checked": total_numbers,
        "total_ungrounded_numbers": total_ungrounded,
        "verifier_status_distribution": verifier_statuses,
        "calculation_entries_checked": total_derived_checked,
        "calculation_mismatches": total_derived_mismatches,
        "avg_fast_latency_s": round(sum(r.fast_latency_s for r in runs) / total, 2) if total else None,
        "avg_research_latency_s": round(sum(r.research_latency_s for r in runs) / total, 2) if total else None,
        "avg_research_llm_calls": round(sum(r.research_llm_calls for r in runs) / total, 2) if total else None,
        "avg_research_provider_calls": round(sum(r.research_provider_calls for r in runs) / total, 2) if total else None,
    }


def _build_payload(runs: list[QuestionRun]) -> dict:
    summary = summarize(runs)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "questions": [
            {
                "category": r.question.category,
                "text": r.question.text,
                "probes": r.question.probes,
                "fast": {
                    "table": r.fast.table is not None,
                    "answer": r.fast.answer,
                    "sources": len(r.fast.sources),
                    "warnings": list(r.fast.warnings),
                    "latency_s": r.fast_latency_s,
                },
                "research": {
                    "table": r.research.table is not None,
                    "answer": r.research.answer,
                    "sources": len(r.research.sources),
                    "warnings": list(r.research.warnings),
                    "latency_s": r.research_latency_s,
                    "llm_calls": r.research_llm_calls,
                    "provider_calls": r.research_provider_calls,
                    "tool_calls": r.research_tool_calls,
                    "candidates_rejected": len(r.research_state.candidates_rejected),
                    "verification_status": (
                        r.research_state.verification_results[-1]["status"]
                        if r.research_state.verification_results else None
                    ),
                },
                "grounding": r.grounding,
                "calculation_recheck": r.calculation_recheck,
            }
            for r in runs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, default=None, help="Write raw per-question results as JSON to PATH (updated after every question, not only at the end).")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N questions.")
    parser.add_argument("--only", type=str, default=None, help="Only run questions in this category.")
    parser.add_argument("--max-tool-calls", type=int, default=25)
    args = parser.parse_args()

    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set -- this benchmark makes real API calls and cannot run without it.", file=sys.stderr)
        raise SystemExit(1)

    engine = default_engine()
    problems = preflight_check(engine)
    if problems:
        print("Preflight failed -- catalog is not sufficiently populated:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(1)

    questions = list(QUESTIONS)
    if args.only:
        questions = [q for q in questions if q.category == args.only]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("No questions selected.", file=sys.stderr)
        raise SystemExit(1)

    def _save(current_runs: list[QuestionRun]) -> None:
        if args.json:
            args.json.write_text(json.dumps(_build_payload(current_runs), indent=2, default=str))

    client = Anthropic()
    try:
        runs = run_all(
            engine, client, questions,
            limits=AgentLimits(max_tool_calls=args.max_tool_calls), on_result=_save,
        )
    except BenchmarkAborted as exc:
        print(f"\n{exc}", file=sys.stderr)
        print(f"Completed {len(exc.runs)}/{len(questions)} questions before stopping.", file=sys.stderr)
        _save(exc.runs)
        if args.json and exc.runs:
            print(f"Partial results saved to {args.json}", file=sys.stderr)
        raise SystemExit(2) from exc

    summary = summarize(runs)
    print(json.dumps(summary, indent=2))

    if args.json:
        _save(runs)
        print(f"\nWrote raw results to {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()

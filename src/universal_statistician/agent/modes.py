"""Fast / Research / Auto execution modes (Phase 5).

Two execution paths, not one expensive loop forced onto every question:

- **Fast mode** is core/ask.py's existing, unchanged legacy pipeline
  (plan once -> search -> deterministic Top-1 select -> retrieve ->
  validate -> answer) — low latency, low LLM/tool usage, right for a
  simple direct lookup ("Population of Azerbaijan in 2024").
- **Research mode** runs agent/loop.py's iterative StatisticalAgent —
  right for multi-source comparison, ambiguous concepts, multi-series
  calculations, and explanatory ("why...") questions.

`select_mode()` picks between them from the question text when the
caller asks for "auto" (the default); a caller may always override
explicitly with "fast" or "research" (task section 7's "explicit API
override").

Research mode's answer text/chart here are still built deterministically
from the investigation's table (reusing core/ask.py's own table->text/
citations helpers, which take a plain ComparisonTable and don't need a
QueryPlan) — Phase 6 replaces the *text* with a separate LLM answer-
writing pass constrained to the same validated evidence; nothing about
retrieval, calculation, or validation changes then.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_statistician.agent.llm import LLMAgent
from universal_statistician.agent.loop import AgentLimits, StatisticalAgent
from universal_statistician.agent.state import InvestigationState
from universal_statistician.core.answer import AskResult, ChartSeries, ChartSpec
from universal_statistician.core.ask import (
    _build_answer_text,
    _provenance_for_latest_period,
    _unique_sources,
    answer_question,
)
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.validation import validate_table
from universal_statistician.planning.base import LLMPlanner

#: Phrases that signal a question needs iterative, multi-source
#: investigation rather than one direct lookup — task section 7's own
#: list ("why", "compare sources", "what drove", "contribution", ...).
#: Deliberately a plain substring heuristic, not another LLM call: mode
#: selection itself must stay cheap, or "auto" would cost as much as
#: research mode for every question.
_RESEARCH_SIGNAL_PHRASES = (
    "why",
    "compare",
    "which source",
    "which official",
    "what drove",
    "contribut",  # contribute/contributed/contribution
    "discrepanc",
    "differ",
    "disagree",
    "breakdown",
    "share of",
    " vs ",
    " vs.",
    "versus",
    "real vs",
    "nominal vs",
    "comparable",
    "decompos",
)

VALID_MODES = ("auto", "fast", "research")


def select_mode(question: str, requested: str = "auto") -> str:
    """Resolve "auto"/"fast"/"research" to the mode that will actually run.
    Raises ValueError for anything else — an unrecognized mode should fail
    loudly, not silently fall back to one or the other."""
    normalized = (requested or "auto").strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(f"Unknown mode {requested!r}; expected one of {VALID_MODES}.")
    if normalized != "auto":
        return normalized

    lowered = question.lower()
    if any(phrase in lowered for phrase in _RESEARCH_SIGNAL_PHRASES):
        return "research"
    return "fast"


def run_fast_mode(
    engine: QueryEngine, question: str, planner: LLMPlanner | None = None
) -> AskResult:
    """The legacy single-pass pipeline, unchanged — see
    docs/architecture/agent-migration-note.md."""
    return answer_question(engine, question, planner=planner)


def _chart_spec_from_state(state: InvestigationState) -> ChartSpec | None:
    base_columns = [c for c in state.table.columns if not c.derived]
    if not base_columns:
        return None
    sources = sorted({c.attribution.source_name for c in base_columns if c.attribution})
    return ChartSpec(
        chart_type="comparison" if len(base_columns) > 1 else "line",
        title=state.question,
        x_axis="period",
        y_axis=base_columns[0].unit or "value",
        units=base_columns[0].unit,
        series=tuple(ChartSeries(key=c.key, label=c.label) for c in base_columns),
        source_note="; ".join(sources) if sources else "",
    )


def run_research_mode(
    engine: QueryEngine,
    llm_agent: LLMAgent,
    question: str,
    *,
    limits: AgentLimits | None = None,
) -> tuple[AskResult, InvestigationState]:
    """Run the iterative StatisticalAgent, then apply the same deterministic
    validation gate every path in this project applies before presenting a
    numerical answer — the orchestrator runs this unconditionally, it never
    depends on the LLM having remembered to call the `validate` tool itself.

    Returns (AskResult, InvestigationState) — the state is the full audit
    trail (task section 15's `debug=true` payload), kept separate from the
    AskResult callers get by default."""
    agent = StatisticalAgent(engine, llm_agent, limits=limits)
    state = agent.investigate(question)

    validation = None
    if state.table.columns:
        validation = validate_table(state.table)
        state.validation_results.append(validation.as_dict())

    table_dict = state.table.as_dict() if state.table.columns else None
    chart = _chart_spec_from_state(state) if state.table.columns else None

    return (
        AskResult(
            question=question,
            query_plan=state.evidence_package(),
            answer=_build_answer_text(state.table if state.table.columns else None, validation),
            table=table_dict,
            chart=chart.as_dict() if chart else None,
            sources=_unique_sources(state.table) if state.table.columns else (),
            provenance=_provenance_for_latest_period(state.table) if state.table.columns else (),
            warnings=tuple(state.warnings),
            validation=validation.as_dict() if validation else None,
        ),
        state,
    )


@dataclass(frozen=True)
class ModeRunResult:
    result: AskResult
    mode_used: str
    investigation: InvestigationState | None = None


def answer_question_with_mode(
    engine: QueryEngine,
    question: str,
    *,
    mode: str = "auto",
    planner: LLMPlanner | None = None,
    llm_agent: LLMAgent | None = None,
    limits: AgentLimits | None = None,
) -> ModeRunResult:
    """The single entry point api.py's /ask (Phase 8) calls: resolves the
    mode, runs the matching path, and returns a uniform result shape.

    Research mode requires an `llm_agent` (there is no "iteratively
    investigate without an LLM" fallback — the entire point of research
    mode is the model driving the loop); falls back to fast mode with a
    warning if one wasn't supplied, the same "remain operational without
    an LLM configured" principle RuleBasedPlanner already applies to fast
    mode's own planning step.
    """
    resolved = select_mode(question, mode)

    if resolved == "research" and llm_agent is None:
        result = run_fast_mode(engine, question, planner=planner)
        result = AskResult(
            question=result.question,
            query_plan=result.query_plan,
            answer=result.answer,
            table=result.table,
            chart=result.chart,
            sources=result.sources,
            provenance=result.provenance,
            warnings=(
                *result.warnings,
                "Research mode was selected but no LLM agent is configured "
                "(ANTHROPIC_API_KEY unset) — answered with fast mode instead.",
            ),
            validation=result.validation,
        )
        return ModeRunResult(result=result, mode_used="fast")

    if resolved == "research":
        result, state = run_research_mode(engine, llm_agent, question, limits=limits)
        return ModeRunResult(result=result, mode_used="research", investigation=state)

    result = run_fast_mode(engine, question, planner=planner)
    return ModeRunResult(result=result, mode_used="fast")

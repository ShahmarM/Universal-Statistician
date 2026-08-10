"""Fast / Research / Auto execution modes.

Fast mode is core/ask.py's single-pass pipeline; research mode is the
iterative StatisticalAgent. select_mode() resolves "auto" from question
text. With an `answer_writer`, LLM prose (grounding-checked, deterministic
fallback) replaces the deterministic answer text; with a `verifier`, a
FAIL verdict sends the investigator back for a bounded retry, and if the
last round still FAILs the answer becomes UNABLE_TO_VERIFY_TEXT — a
failed numeric draft is never returned as valid.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_statistician.agent.answer_writer import LLMAnswerWriter, write_and_verify_answer
from universal_statistician.agent.llm import LLMAgent
from universal_statistician.agent.loop import AgentLimits, StatisticalAgent
from universal_statistician.agent.state import InvestigationState
from universal_statistician.agent.verifier import LLMVerifier, VerificationReport
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

#: Phrases signalling iterative investigation. A plain substring heuristic
#: — mode selection must stay cheap, not cost an LLM call itself.
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
    """Resolve "auto"/"fast"/"research" to the mode that will run; raises
    ValueError for anything else."""
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
    """The legacy single-pass pipeline, unchanged."""
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


#: Returned as `answer` when the last verification round still FAILs.
#: Contains no numbers, so it can't itself be an unsupported claim; the
#: failing issues remain in warnings/verification_results.
UNABLE_TO_VERIFY_TEXT = (
    "I could not verify this answer with confidence, even after a follow-up "
    "investigation. Automated verification found unresolved issues with the "
    "draft answer, so I'm not presenting a number here as confirmed — see "
    "the warnings and verification results for what specifically failed and "
    "the retrieved data for manual review."
)


def _retry_instruction(question: str, report: VerificationReport) -> str:
    issue_lines = "\n".join(f"- {issue.category}: {issue.detail}" for issue in report.issues)
    return (
        f"{question}\n\nA verification pass reviewed your previous investigation and found "
        f"problems that must be fixed before the answer can be presented:\n{issue_lines}\n\n"
        "Continue investigating to address these specifically — retrieve/inspect/calculate "
        "whatever is missing, reject a candidate that turned out wrong, or add an assumption/"
        "warning explaining a genuine limitation. Reuse already-retrieved result_ids where "
        "they're still valid."
    )


def run_research_mode(
    engine: QueryEngine,
    llm_agent: LLMAgent,
    question: str,
    *,
    limits: AgentLimits | None = None,
    answer_writer: LLMAnswerWriter | None = None,
    verifier: LLMVerifier | None = None,
    max_verification_rounds: int = 2,
) -> tuple[AskResult, InvestigationState]:
    """Run the iterative StatisticalAgent, always applying the
    deterministic validation gate (never dependent on the LLM having
    called `validate` itself). See the module docstring for how
    `answer_writer`/`verifier` shape the returned answer. Returns
    (AskResult, InvestigationState) — the state is the full audit trail."""
    agent = StatisticalAgent(engine, llm_agent, limits=limits)
    state: InvestigationState | None = None
    validation = None
    answer_text = ""
    report: VerificationReport | None = None

    rounds = max(1, max_verification_rounds) if verifier is not None else 1
    for round_number in range(1, rounds + 1):
        if state is None:
            state = agent.investigate(question)
        else:
            assert report is not None
            state = agent.investigate(question, state=state, instruction=_retry_instruction(question, report))

        validation = None
        if state.table.columns:
            validation = validate_table(state.table)
            state.validation_results.append(validation.as_dict())

        fallback_text = _build_answer_text(state.table if state.table.columns else None, validation)
        answer_text = fallback_text
        if answer_writer is not None:
            write_result = write_and_verify_answer(
                state.evidence_package(), answer_writer, fallback_text=fallback_text
            )
            answer_text = write_result.text
            if not write_result.llm_written and (write_result.ungrounded_numbers or write_result.citation_problems):
                state.warnings.append(
                    "LLM answer-writer produced ungrounded numbers "
                    f"{write_result.ungrounded_numbers} or incompatible citations "
                    f"{write_result.citation_problems}; used the deterministic answer instead."
                )

        if verifier is None:
            break

        report = verifier.verify(question=question, evidence=state.evidence_package(), draft_answer=answer_text)
        state.verification_results.append(report.as_dict())

        if report.status != "FAIL":
            if report.status == "WARNING":
                for issue in report.issues:
                    state.warnings.append(f"Verification warning ({issue.category}): {issue.detail}")
            break

        if round_number == rounds:
            issue_summary = "; ".join(f"{issue.category}: {issue.detail}" for issue in report.issues)
            state.warnings.append(
                f"Verification failed after {rounds} round(s) and could not be resolved: "
                f"{issue_summary or 'no specific issues reported'}."
            )
            # A draft that failed verification is never handed back as valid.
            answer_text = UNABLE_TO_VERIFY_TEXT
            break

    assert state is not None
    table_dict = state.table.as_dict() if state.table.columns else None
    chart = _chart_spec_from_state(state) if state.table.columns else None

    return (
        AskResult(
            question=question,
            query_plan=state.evidence_package(),
            answer=answer_text,
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
    answer_writer: LLMAnswerWriter | None = None,
    verifier: LLMVerifier | None = None,
    max_verification_rounds: int = 2,
) -> ModeRunResult:
    """The single entry point /ask calls: resolve the mode, run the
    matching path, return a uniform shape. Research mode needs an
    `llm_agent`; without one it falls back to fast mode with a warning."""
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
        result, state = run_research_mode(
            engine,
            llm_agent,
            question,
            limits=limits,
            answer_writer=answer_writer,
            verifier=verifier,
            max_verification_rounds=max_verification_rounds,
        )
        return ModeRunResult(result=result, mode_used="research", investigation=state)

    result = run_fast_mode(engine, question, planner=planner)
    return ModeRunResult(result=result, mode_used="fast")

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

Research mode's chart is still built deterministically from the
investigation's table (reusing core/ask.py's own citations helpers, which
take a plain ComparisonTable and don't need a QueryPlan). The answer text
is deterministic (`_build_answer_text`) unless an `answer_writer` is
supplied, in which case Phase 6's `write_and_verify_answer()` replaces it
with LLM prose constrained to the same validated evidence and checked for
unsupported numbers, falling back to the deterministic text on failure.

If a `verifier` (agent/verifier.py's LLMVerifier, Phase 7) is also
supplied, the draft answer goes through one further independent check
before being returned: on a FAIL verdict, the investigator gets one more
bounded round (reusing already-retrieved evidence, see
StatisticalAgent.investigate()'s `state`/`instruction` parameters) to
address the reported issues, then the answer is rebuilt and re-verified,
up to `max_verification_rounds` — never an unbounded back-and-forth, and
AgentLimits still bounds the underlying tool-call budget across every
round combined, not per round. If the LAST round still FAILs, the numeric
answer is never returned as if it were valid: `answer` becomes a fixed
`UNABLE_TO_VERIFY_TEXT` instead of the draft/fallback text — a verifier
that caught a real problem must not be silently overridden just because
the retry budget ran out.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_statistician.agent.answer_writer import write_and_verify_answer
from universal_statistician.agent.llm import LLMAgent, LLMAnswerWriter
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


#: Returned as `answer` (never the draft/fallback text) when the LAST
#: verification round still FAILs — deliberately contains no numbers of
#: its own, so it can never itself become an unsupported numerical claim.
#: The specific issues that caused the failure are still fully available
#: in `state.warnings`/`state.verification_results` (and the API's
#: `verification` field) for a human to review; they just never get
#: presented as part of a confirmed answer.
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
    """Run the iterative StatisticalAgent, then apply the same deterministic
    validation gate every path in this project applies before presenting a
    numerical answer — the orchestrator runs this unconditionally, it never
    depends on the LLM having remembered to call the `validate` tool itself.

    If `answer_writer` is supplied, the deterministic table-derived text is
    used only as the fallback for Phase 6's `write_and_verify_answer()` —
    the returned answer is LLM prose constrained to the same evidence and
    checked for unsupported numbers, never unchecked. Without one, the
    deterministic text is used as the answer directly.

    If `verifier` is also supplied, that answer then goes through Phase 7's
    independent semantic check; a FAIL sends the investigator back for one
    more bounded round (see `_retry_instruction`) before rebuilding and
    re-verifying, up to `max_verification_rounds`. If it still hasn't
    passed when the bound is reached, `answer` becomes `UNABLE_TO_VERIFY_TEXT`
    — the failed numeric draft is never returned as if it were a valid
    answer, on the same "never silently return unsupported prose" principle
    Phase 6's answer-writer guard already applies to a single number.

    Returns (AskResult, InvestigationState) — the state is the full audit
    trail (task section 15's `debug=true` payload), kept separate from the
    AskResult callers get by default."""
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
            if not write_result.llm_written and write_result.unsupported_numbers:
                state.warnings.append(
                    "LLM answer-writer produced unsupported numbers "
                    f"{write_result.unsupported_numbers}; used the deterministic answer instead."
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
            # The draft answer failed verification and retrying didn't fix
            # it -- it must never be handed back as though it were valid,
            # numbers and all (task: "A final verifier FAIL must never
            # return the failed numerical answer as valid").
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

"""StatisticalAgent: the iterative investigation loop.

question -> LLM -> tool calls -> tool results -> ... -> auditable
InvestigationState. The investigator's own final free text is debug-only
(investigator_summary); the answer writer builds the real answer from the
validated evidence in a separate, tool-less call, so a stray sentence here
can never become an unverified claim.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from universal_statistician.agent.llm import LLMAgent
from universal_statistician.agent.state import InvestigationState, ToolCallRecord
from universal_statistician.agent.tools import TOOL_SCHEMAS, timed_dispatch_tool
from universal_statistician.core.engine import QueryEngine

logger = logging.getLogger(__name__)

INVESTIGATOR_SYSTEM_PROMPT = (
    "You are a statistical investigator. Answer the user's question by calling "
    "tools to find, inspect, retrieve, and calculate official statistics — never "
    "state a number, indicator code, or country code from memory.\n\n"
    "Rules:\n"
    "- search_series only ranks by lexical/metadata match, not correctness for "
    "this question. Inspect candidates (inspect_series) before choosing one — "
    "a bare 'GDP' search commonly returns nominal-price, real-price, and "
    "per-capita variants together; picking the top result blindly is wrong "
    "unless you have actually confirmed it matches what the question asks for "
    "(e.g. 'real GDP growth' needs a constant-price series, not current-price).\n"
    "- If a candidate doesn't fit, call reject_candidate with a short reason "
    "before moving on, so the reasoning is recorded.\n"
    "- retrieve_series is the only source of numeric values. calculate() only "
    "accepts result_id values already returned by retrieve_series/calculate — "
    "never invent one, never supply a number yourself.\n"
    "- For a multi-country or multi-source comparison, select the SAME concept/"
    "definition for every geography or source before comparing — comparing a "
    "nominal series for one country against a real series for another silently "
    "makes the answer wrong.\n"
    "- Call validate() before concluding, on whatever result_ids matter to the "
    "final answer. A FAIL means those numbers cannot be presented as a "
    "supported answer — investigate further or explain the limitation.\n"
    "- Use compare_series when the question is about why two sources or series "
    "disagree; it returns evidence (units, frequency, price basis, numeric "
    "differences), never a verdict — do not assert a methodological explanation "
    "the evidence doesn't state.\n"
    "- When you have enough validated evidence, stop calling tools and write a "
    "brief internal summary of what you found and which result_ids answer the "
    "question — this summary is for audit purposes only, not shown to the user "
    "verbatim, so it does not need to be polished prose."
)


@dataclass(frozen=True)
class AgentLimits:
    """Stopping rules — generous enough for a multi-source investigation,
    never unbounded."""

    max_tool_calls: int = 25
    max_iterations: int = 15
    max_candidates_inspected: int = 12
    max_provider_calls: int = 15
    timeout_seconds: float = 120.0


def _requested_geography_count(tool_input: dict) -> int:
    """Provider requests a pending retrieve_series call would make — one
    per geography. Malformed input counts as 0; retrieve_series reports
    the real error when it runs."""
    geographies = tool_input.get("geographies") if isinstance(tool_input, dict) else None
    return len(geographies) if isinstance(geographies, list) else 0


def _summarize_tool_output(result: dict) -> dict:
    """Small, debug-safe summary of a tool result for tool_call_history;
    full payloads stay in the conversation messages only."""
    summary = dict(result)
    for key in ("candidates", "results"):
        value = summary.get(key)
        if isinstance(value, list):
            summary[key] = f"{len(value)} item(s)"
    values = summary.get("values")
    if isinstance(values, dict):
        summary["values"] = f"{len(values)} period(s)"
    return summary


class StatisticalAgent:
    def __init__(
        self,
        engine: QueryEngine,
        llm: LLMAgent,
        *,
        limits: AgentLimits | None = None,
        system_prompt: str = INVESTIGATOR_SYSTEM_PROMPT,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.limits = limits or AgentLimits()
        self.system_prompt = system_prompt

    def investigate(
        self,
        question: str,
        *,
        state: InvestigationState | None = None,
        instruction: str | None = None,
    ) -> InvestigationState:
        """Run the investigation loop. Pass `state` + `instruction` to
        continue a prior investigation (verifier retries do): counters keep
        accumulating, so AgentLimits bound the whole investigation."""
        resuming = state is not None
        if state is None:
            state = InvestigationState(question=question, engine=self.engine)
            messages: list[dict] = [{"role": "user", "content": question}]
        else:
            prompt = instruction or question
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\nEvidence already gathered so far (reuse these "
                        f"result_ids where they're still valid instead of "
                        f"re-retrieving them):\n{json.dumps(state.evidence_package(), default=str)}"
                    ),
                }
            ]
        started_at = time.monotonic()
        investigator_summary = state.investigator_summary if resuming else ""

        while True:
            state.iteration_count += 1
            if state.iteration_count > self.limits.max_iterations:
                state.warnings.append(
                    f"Investigation stopped: reached max_iterations ({self.limits.max_iterations})."
                )
                break
            if time.monotonic() - started_at > self.limits.timeout_seconds:
                state.warnings.append(
                    f"Investigation stopped: reached timeout_seconds ({self.limits.timeout_seconds})."
                )
                break

            try:
                response = self.llm.step(
                    system=self.system_prompt, messages=messages, tools=TOOL_SCHEMAS
                )
            except Exception as exc:  # noqa: BLE001 - reported, investigation ends cleanly
                state.warnings.append(f"Investigation stopped: LLM call failed: {exc}")
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            investigator_summary = "".join(
                b.text for b in response.content if b.type == "text"
            ) or investigator_summary

            if not tool_use_blocks:
                break  # the model concluded its investigation

            tool_results = []
            for block in tool_use_blocks:
                if len(state.tool_call_history) >= self.limits.max_tool_calls:
                    result, duration_ms = (
                        {
                            "error": (
                                f"max_tool_calls limit ({self.limits.max_tool_calls}) reached; "
                                "no further tools will be executed this investigation."
                            )
                        },
                        0.0,
                    )
                elif block.name == "inspect_series" and self._count(state, "inspect_series") >= (
                    self.limits.max_candidates_inspected
                ):
                    result, duration_ms = (
                        {
                            "error": (
                                f"max_candidates_inspected limit "
                                f"({self.limits.max_candidates_inspected}) reached."
                            )
                        },
                        0.0,
                    )
                elif block.name == "retrieve_series" and (
                    state.provider_call_count + _requested_geography_count(block.input)
                    > self.limits.max_provider_calls
                ):
                    # Prospective: one call for many geographies is many
                    # provider requests, not one.
                    result, duration_ms = (
                        {
                            "error": (
                                f"max_provider_calls limit ({self.limits.max_provider_calls}) "
                                f"would be exceeded: {state.provider_call_count} provider "
                                f"request(s) already made, this call requests "
                                f"{_requested_geography_count(block.input)} more. Request fewer "
                                "geographies per call, reuse already-retrieved result_ids, or "
                                "conclude the investigation."
                            )
                        },
                        0.0,
                    )
                else:
                    result, duration_ms = timed_dispatch_tool(state, block.name, block.input)

                state.tool_call_history.append(
                    ToolCallRecord(
                        iteration=state.iteration_count,
                        tool_name=block.name,
                        input=block.input,
                        output_summary=_summarize_tool_output(result),
                        duration_ms=duration_ms,
                    )
                )
                logger.info(
                    "agent.tool_call",
                    extra={
                        "tool_name": block.name,
                        "iteration": state.iteration_count,
                        "duration_ms": duration_ms,
                        "is_error": bool(result.get("error")),
                    },
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                        "is_error": bool(result.get("error")),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

            if len(state.tool_call_history) >= self.limits.max_tool_calls:
                state.warnings.append(
                    f"Investigation stopped: reached max_tool_calls ({self.limits.max_tool_calls})."
                )
                break

        state.investigator_summary = investigator_summary
        logger.info(
            "agent.investigation_completed",
            extra={
                "iterations": state.iteration_count,
                "tool_calls": len(state.tool_call_history),
                "retrieved_results": len(state.retrieved),
                "derived_results": len(state.derived),
                "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
            },
        )
        return state

    @staticmethod
    def _count(state: InvestigationState, tool_name: str) -> int:
        return sum(1 for record in state.tool_call_history if record.tool_name == tool_name)

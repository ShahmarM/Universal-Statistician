"""Deterministic fallback planner: no LLM, no external dependency.

Exists so the platform "remains operational for structured/manual queries
without an LLM" (section 22) — e.g. no ANTHROPIC_API_KEY configured, or a
caller that wants a plan without any model call. It cannot understand
natural language, so it makes the narrowest possible interpretation (the
whole question as one literal catalog search phrase) and says so explicitly
via `assumptions` rather than pretending to have understood anything.
"""

from __future__ import annotations

from universal_statistician.core.query_plan import QuestionInterpretation


class RuleBasedPlanner:
    def interpret(self, question: str) -> QuestionInterpretation:
        return QuestionInterpretation(
            concepts=(question.strip(),) if question.strip() else (),
            assumptions=(
                "No language model configured: the question was used verbatim as a "
                "single catalog search phrase, with no geography/period/transformation "
                "inference. Configure an LLMPlanner (e.g. AnthropicPlanner) for real "
                "natural-language interpretation.",
            ),
        )

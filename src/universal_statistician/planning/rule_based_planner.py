"""Deterministic fallback planner: no LLM, no external dependency, so the
platform stays usable with no API key configured. It cannot understand
natural language, so it takes the narrowest possible interpretation — the
whole question as one literal search phrase — and says so in `assumptions`
rather than pretending to have understood more.
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

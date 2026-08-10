"""The LLMPlanner abstraction: core functionality is never hard-coded
around one LLM provider and keeps working with none configured.

A Protocol rather than an ABC, so planners as different as
RuleBasedPlanner (no dependencies) and AnthropicPlanner (wraps the SDK)
need no shared base class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from universal_statistician.core.query_plan import QuestionInterpretation


@runtime_checkable
class LLMPlanner(Protocol):
    def interpret(self, question: str) -> QuestionInterpretation:
        """Read a natural-language question into concepts/geographies/
        periods/transformations/output shape/assumptions — never an
        indicator code (see core/query_plan.py's module docstring: only
        QueryEngine.search_indicator(), called by build_query_plan(), may
        supply those)."""
        ...

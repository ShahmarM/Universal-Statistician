"""The LLMPlanner abstraction (section 22): core statistical functionality
must not be hard-coded around one LLM provider, and must keep working for
structured/manual queries with no LLM configured at all.

A Protocol (structural typing, like providers.base.MetadataDiscoverable)
rather than an ABC: any object with a matching `interpret()` method works,
without forcing a shared base class on planners as different as
RuleBasedPlanner (no external dependency at all) and AnthropicPlanner
(wraps the Anthropic SDK) — the same reasoning that kept
MetadataDiscoverable structural rather than a mixin.
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

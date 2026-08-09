"""LLMPlanner backed by the Claude API, via a forced tool call rather than
free text — the model can only respond by filling in this schema's fields,
which excludes any place to put a fabricated indicator code or a fabricated
number. Mirrors chat.py's conventions: the Anthropic client is injected
(never constructed here), so this is testable with a fake client built from
real anthropic.types objects, no ANTHROPIC_API_KEY or network required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from universal_statistician.core.query_plan import QuestionInterpretation

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024

PLAN_TOOL_NAME = "propose_query_plan"

# section 11 (ambiguity handling) + section 23 (anti-hallucination), turned
# into an instruction: infer the conventional reading when confident, say so
# in `assumptions`, and only set needs_clarification when the interpretations
# would materially change the result — never invent an indicator code, only
# natural-language concepts for the catalog to resolve.
SYSTEM_PROMPT = (
    "You interpret natural-language statistical questions into a structured "
    "query plan. You never state a statistic, an indicator code, or a country "
    "code you are not certain of from general knowledge — indicator codes in "
    "particular are always resolved later against an official catalog, never "
    "supplied by you; `concepts` must be plain natural-language search phrases "
    "(e.g. 'GDP per capita', 'youth unemployment rate'), never codes. "
    "When a question is ambiguous (e.g. 'GDP growth' could mean real or "
    "nominal, annual or quarterly), infer the most conventional reading, "
    "record it explicitly in `assumptions` as a short plain-language note, "
    "and only set needs_clarification=true with a specific "
    "clarification_question when the possible interpretations would give "
    "materially different results — do not ask for clarification on minor "
    "ambiguities you can reasonably resolve yourself."
)

PLAN_TOOL_SCHEMA = {
    "name": PLAN_TOOL_NAME,
    "description": (
        "Propose a structured interpretation of the user's statistical question. "
        "Every field is your interpretation, not a data value: indicator codes are "
        "never supplied here, only natural-language concepts for a catalog search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "concepts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Natural-language statistical concepts to search an official "
                    "indicator catalog for, e.g. ['GDP per capita']. Never an "
                    "indicator code."
                ),
            },
            "geographies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Countries/areas the question concerns, in whatever form the user used them (names or codes).",
            },
            "start_period": {"type": ["string", "null"]},
            "end_period": {"type": ["string", "null"]},
            "frequency": {
                "type": ["string", "null"],
                "description": "e.g. 'A' (annual), 'Q' (quarterly), 'M' (monthly), if the question implies one.",
            },
            "transformations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "e.g. ['yoy_growth', 'cumulative_growth', 'ratio', 'rank'] if the question asks for a computed value rather than a raw observation.",
            },
            "comparison": {
                "type": ["string", "null"],
                "enum": ["cross_country", "cross_indicator", None],
                "description": "'cross_country' for one concept across several geographies, 'cross_indicator' for several concepts in one geography, null otherwise.",
            },
            "ranking": {"type": "boolean", "description": "True if the question asks for a ranking (e.g. 'which countries had the highest...')."},
            "output_type": {
                "type": "string",
                "enum": ["answer", "table", "chart", "comparison_table"],
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short, plain-language notes on any interpretation you inferred rather than the user stating explicitly.",
            },
            "needs_clarification": {"type": "boolean"},
            "clarification_question": {"type": ["string", "null"]},
        },
        "required": ["concepts", "geographies", "transformations", "output_type", "assumptions", "needs_clarification"],
    },
}


@dataclass
class AnthropicPlanner:
    client: Any
    model: str = DEFAULT_MODEL

    def interpret(self, question: str) -> QuestionInterpretation:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
            tools=[PLAN_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": PLAN_TOOL_NAME},
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return QuestionInterpretation.from_dict(tool_use.input)

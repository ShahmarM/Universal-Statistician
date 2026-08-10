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
    "ambiguities you can reasonably resolve yourself.\n\n"
    "Each entry in `transformations` describes WHAT statistical operation is "
    "required — never a number, never a computed result. Simple operations "
    "(growth, yoy_growth, period_over_period_growth, absolute_change, "
    "pp_change, cagr, cumulative_growth, rank) only need `operation` set. "
    "Operations that combine two series (share, per_capita, difference) need "
    "the concept fields their schema describes (e.g. share's "
    "numerator_concept/denominator_concept) — write each referenced concept "
    "as a natural-language phrase exactly like `concepts`, e.g. 'non-oil GDP' "
    "or 'total GDP', never an indicator code; you do not need to also repeat "
    "it in the top-level `concepts` array, it gets resolved through the "
    "catalog either way. `index` needs input_concept and base_period (e.g. "
    "'2015'); base_value defaults to 100 if omitted. `weighted_average` "
    "combines a single concept's values across several of the question's "
    "geographies — `inputs` must be geography/country codes already present "
    "in `geographies`, one per entry of `weights`, not concepts.\n\n"
    "For `geographies` (and weighted_average's `inputs`), prefer ISO 3166-1 "
    "alpha-3 country codes when you are confident of one (e.g. 'AZE' for "
    "Azerbaijan) — the system also resolves plain country names on its own, "
    "so a name is an acceptable fallback, but a correct code avoids any "
    "ambiguity. For `start_period`/`end_period`, use an actual period value "
    "(e.g. '2015', '2020-Q1') or leave the field null/omitted when the "
    "question doesn't specify one (e.g. 'the latest available year') — "
    "never write words like 'latest', 'present', or 'current' as the period "
    "value itself; the system resolves an omitted period to whatever is "
    "actually latest/earliest in the retrieved data."
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
                "description": (
                    "Countries/areas the question concerns. Prefer ISO 3166-1 alpha-3 "
                    "codes (e.g. 'AZE') when confident; a plain country name is an "
                    "acceptable fallback, the system resolves it."
                ),
            },
            "start_period": {
                "type": ["string", "null"],
                "description": "An actual period value (e.g. '2015') or null - never the word 'latest'/'present'/'current'.",
            },
            "end_period": {
                "type": ["string", "null"],
                "description": "An actual period value (e.g. '2024') or null - never the word 'latest'/'present'/'current'.",
            },
            "frequency": {
                "type": ["string", "null"],
                "description": "e.g. 'A' (annual), 'Q' (quarterly), 'M' (monthly), if the question implies one.",
            },
            "transformations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "growth", "yoy_growth", "period_over_period_growth",
                                "absolute_change", "pp_change", "cagr", "cumulative_growth",
                                "rank", "share", "per_capita", "difference", "index",
                                "weighted_average",
                            ],
                        },
                        "numerator_concept": {
                            "type": ["string", "null"],
                            "description": "share/per_capita only: natural-language concept, never a code.",
                        },
                        "denominator_concept": {
                            "type": ["string", "null"],
                            "description": "share/per_capita only: natural-language concept, never a code.",
                        },
                        "input_concept": {
                            "type": ["string", "null"],
                            "description": "index only: natural-language concept, never a code.",
                        },
                        "base_period": {
                            "type": ["string", "null"],
                            "description": "index only: the period rebased to base_value, e.g. '2015'.",
                        },
                        "base_value": {
                            "type": ["number", "null"],
                            "description": "index only: defaults to 100 if omitted.",
                        },
                        "left_concept": {
                            "type": ["string", "null"],
                            "description": "difference only: natural-language concept, never a code.",
                        },
                        "right_concept": {
                            "type": ["string", "null"],
                            "description": "difference only: natural-language concept, never a code.",
                        },
                        "inputs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "weighted_average only: geography/country codes already in `geographies`, not concepts.",
                        },
                        "weights": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "weighted_average only: one weight per entry of `inputs`, same order.",
                        },
                        "output_name": {
                            "type": ["string", "null"],
                            "description": "Optional human-readable name for the result, e.g. 'non-oil share of GDP'.",
                        },
                    },
                    "required": ["operation"],
                },
                "description": (
                    "Statistical operations the question asks for, each describing WHAT to "
                    "compute — never a number. e.g. [{'operation': 'cumulative_growth'}] for "
                    "'cumulative GDP growth', or [{'operation': 'share', 'numerator_concept': "
                    "'non-oil GDP', 'denominator_concept': 'total GDP'}] for a share question."
                ),
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

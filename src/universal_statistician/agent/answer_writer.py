"""Separate answer-writing pass + evidence-ID grounding guard (task section 2).

The investigator (agent/loop.py) never writes the user-facing answer —
its own final turn is debug-only (InvestigationState.investigator_summary,
see that module's docstring). This module is where prose actually gets
written, and it is deliberately constrained more tightly than the
investigator:

- Input is a plain JSON evidence package (InvestigationState.
  evidence_package()) — no tools, no message history, no ability to ask
  for more data. The writer cannot retrieve, calculate, or investigate;
  it can only describe what's already there.
- Output is a forced tool call (mirroring planning/anthropic_planner.py
  and agent/verifier.py), never free text: `text` (the answer) plus
  `citations` — one entry per number `text` states, each naming the exact
  `evidence_id` (agent/evidence.py) it came from and what the model
  *believes* that evidence_id's geography/period/value_kind are.
- `check_answer_grounding()` then verifies every citation against the
  *actual* evidence_id it names — not "does this number exist somewhere,"
  but "does this exact cell exist, does its value match what was stated,
  and do its real geography/period/value_kind match what the citation
  claims." A claim like "Georgia GDP was 72.4" fails here if 72.4's real
  evidence_id belongs to Azerbaijan, even though 72.4 genuinely is
  somewhere in the evidence package — the old flat "collect every number
  from the table and check membership" guard could never catch that; a
  per-cell identity a claim must point to, and Python checks, can.
- `write_and_verify_answer()` retries a bounded number of times on an
  ungrounded/incompatible citation, then falls back to a supplied
  deterministic answer instead of ever returning unchecked prose —
  "do not silently return unsupported prose" (task section 9) is enforced
  here, not left to the prompt alone.

Honest limitation (see agent/evidence.py's docstring for the full
statement): this checks a citation's *declared* geography/period/kind
against the real evidence, which is a strong forcing function but not
literal NLP over the prose sentence itself — agent/verifier.py's
independent semantic pass is the second, complementary layer for the
residual case where the prose disagrees with its own citation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from universal_statistician.core.geography import resolve_geography

DEFAULT_MODEL = "claude-sonnet-5"

ANSWER_WRITER_SYSTEM_PROMPT = (
    "You write the final answer to a statistical question from ONLY the "
    "validated evidence provided below, as a tool call. You have no other "
    "tools and cannot look anything up — everything you say must come from "
    "this evidence.\n\n"
    "The evidence includes an `evidence` object: evidence_id -> {value, "
    "unit, value_kind, indicator_id, geography, period, source_name, "
    "operation, formula, input_evidence_ids, ...}. Every evidence_id has "
    "the exact shape \"{result_id}@{period}\", e.g. \"result_2@2023\".\n\n"
    "Mandatory rules:\n"
    "- Call the report_answer tool with `text` (your answer) and "
    "`citations`: one citation per number that appears in `text`, each "
    "naming the exact evidence_id it came from.\n"
    "- For each citation, `claimed_geography`/`claimed_period`/"
    "`claimed_value_kind` must describe what YOU believe that evidence_id's "
    "geography/period/value_kind actually are — copy them from the "
    "evidence's own geography/period/value_kind fields for that "
    "evidence_id, exactly. If you're not sure, use the evidence's real "
    "values, not what the question phrased. Getting this wrong on a real "
    "evidence_id is exactly what will be checked and rejected.\n"
    "- Never estimate, compute a new figure, or state a number that isn't "
    "backed by a real evidence_id. Never invent a missing period, country, "
    "or value — if the evidence doesn't cover something the question asks "
    "about, say so plainly in `text` instead of filling the gap (and cite "
    "nothing for that sentence).\n"
    "- value_kind matters: a percent, a percentage_points figure, a "
    "currency amount, an index, a count, and a ratio are not "
    "interchangeable — state which one you mean (e.g. \"grew by 3.2 "
    "percentage points\" vs \"grew by 3.2%\") consistently with the cited "
    "evidence_id's real value_kind.\n"
    "- Mention material items from `warnings` (e.g. a missing geography, a "
    "retrieval failure) rather than omitting them.\n"
    "- If validation results show WARNING or FAIL, reflect that honestly — "
    "do not present a FAILED result as a clean, supported answer.\n"
    "- Briefly explain anything in `assumptions` that shaped the answer.\n"
    "- Cite sources by the organization/source name attached to the data you "
    "use, never a source not present in the evidence.\n"
    "- Be concise and directly address the question asked — no filler, no "
    "unrelated commentary."
)

WRITE_ANSWER_TOOL_NAME = "report_answer"

#: Mirrors agent/evidence.py::VALUE_KINDS — duplicated as a literal tuple
#: (not imported) only so this schema stays a static constant; the two are
#: kept in sync by test_agent_answer_writer.py asserting equality.
_CITATION_VALUE_KINDS = (
    "count", "currency", "index", "level", "percent", "percentage_points", "ratio", "rank", "unknown",
)

WRITE_ANSWER_TOOL_SCHEMA = {
    "name": WRITE_ANSWER_TOOL_NAME,
    "description": "Report the final answer text plus a citation for every number it states.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The answer, addressed to the user."},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence_id": {
                            "type": "string",
                            "description": "Exact evidence_id from the evidence package, e.g. 'result_2@2023'.",
                        },
                        "stated_value": {
                            "type": "string",
                            "description": "The number exactly as written in `text`, e.g. '78.8' or '3.2%'.",
                        },
                        "claimed_geography": {
                            "type": ["string", "null"],
                            "description": "What you believe this evidence_id's geography is — copy from the evidence, not the question.",
                        },
                        "claimed_period": {
                            "type": ["string", "null"],
                            "description": "What you believe this evidence_id's period is — copy from the evidence.",
                        },
                        "claimed_value_kind": {
                            "type": ["string", "null"],
                            "enum": list(_CITATION_VALUE_KINDS),
                            "description": "What kind of quantity this number is — copy from the evidence's value_kind.",
                        },
                    },
                    "required": ["evidence_id", "stated_value"],
                },
            },
        },
        "required": ["text", "citations"],
    },
}

#: Matches a numeric token, with proper thousands-grouped commas (groups of
#: exactly 3 digits) or plain digits, optional decimal part, optional %.
_NUMBER_PATTERN = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?")

#: A bare 4-digit integer in this range is treated as a period/year
#: reference, not a statistic requiring evidence support.
_YEAR_RANGE = range(1900, 2101)


def extract_numbers(text: str) -> list[tuple[float, int]]:
    """Every numeric token in `text`, as (value, decimal_places)."""
    results: list[tuple[float, int]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        token = match.group()
        raw = token.rstrip("%").replace(",", "")
        if not raw or raw == "-":
            continue
        if "." not in token and "," not in token and "%" not in token:
            try:
                year = int(raw)
            except ValueError:
                year = None
            if year is not None and year in _YEAR_RANGE and len(raw.lstrip("-")) == 4:
                continue
        try:
            value = float(raw)
        except ValueError:
            continue
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        results.append((value, decimals))
    return results


def _parse_stated_value(stated_value: str) -> tuple[float, int] | None:
    raw = stated_value.strip().rstrip("%").replace(",", "")
    if not raw or raw in ("-", "."):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    return value, decimals


def _values_match(a: float, a_decimals: int, b: float) -> bool:
    decimals = max(0, min(a_decimals, 6))
    return round(a, decimals) == round(b, decimals)


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    stated_value: str
    claimed_geography: str | None = None
    claimed_period: str | None = None
    claimed_value_kind: str | None = None

    @staticmethod
    def from_dict(payload: dict) -> "Citation":
        return Citation(
            evidence_id=str(payload.get("evidence_id") or ""),
            stated_value=str(payload.get("stated_value") or ""),
            claimed_geography=payload.get("claimed_geography"),
            claimed_period=payload.get("claimed_period"),
            claimed_value_kind=payload.get("claimed_value_kind"),
        )

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "stated_value": self.stated_value,
            "claimed_geography": self.claimed_geography,
            "claimed_period": self.claimed_period,
            "claimed_value_kind": self.claimed_value_kind,
        }


@dataclass(frozen=True)
class AnswerDraft:
    text: str
    citations: tuple[Citation, ...] = ()


@runtime_checkable
class LLMAnswerWriter(Protocol):
    def write(self, *, system: str, evidence: dict) -> AnswerDraft:
        """Produce a grounded answer from a JSON-serializable evidence
        package only — no tools, no message history, no ability to ask a
        follow-up question. Returns `text` plus the `citations` the model
        declared for every number in it (see check_answer_grounding())."""
        ...


@dataclass
class AnthropicAnswerWriter:
    """LLMAnswerWriter backed by the Claude API via a forced tool call
    (mirrors planning/anthropic_planner.py and agent/verifier.py) — the
    model can only respond by filling in WRITE_ANSWER_TOOL_SCHEMA's
    fields, which is what makes `citations` structured data Python can
    check rather than something that would have to be parsed back out of
    free prose. Deliberately no other tools available, unlike
    AnthropicAgent — this role cannot call anything else, by construction,
    not just by prompt. `client` is injected, never constructed here, same
    testability convention as every other LLM role in this package."""

    client: Any
    model: str = DEFAULT_MODEL
    max_tokens: int = 2048

    def write(self, *, system: str, evidence: dict) -> AnswerDraft:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": json.dumps(evidence, default=str)}],
            tools=[WRITE_ANSWER_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": WRITE_ANSWER_TOOL_NAME},
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        payload = tool_use.input
        citations = tuple(Citation.from_dict(c) for c in (payload.get("citations") or ()))
        return AnswerDraft(text=str(payload.get("text") or ""), citations=citations)


def check_citation(citation: Citation, evidence: dict) -> list[str]:
    """Every way one citation can fail to actually ground its number —
    empty list means it's fully compatible. `evidence` is
    InvestigationState.evidence_package()["evidence"] (evidence_id ->
    EvidenceEntry.as_dict())."""
    entry = evidence.get(citation.evidence_id)
    if entry is None:
        return [f"evidence_id {citation.evidence_id!r} does not exist in this investigation's evidence"]

    problems: list[str] = []
    parsed = _parse_stated_value(citation.stated_value)
    if parsed is None:
        problems.append(f"stated_value {citation.stated_value!r} is not a parseable number")
    else:
        stated_value, decimals = parsed
        if not _values_match(stated_value, decimals, entry["value"]):
            problems.append(
                f"stated_value {citation.stated_value!r} does not match evidence_id "
                f"{citation.evidence_id!r}'s actual value {entry['value']!r}"
            )

    if entry.get("geography") and citation.claimed_geography:
        if resolve_geography(citation.claimed_geography).upper() != str(entry["geography"]).upper():
            problems.append(
                f"claimed_geography {citation.claimed_geography!r} does not match evidence_id "
                f"{citation.evidence_id!r}'s actual geography {entry['geography']!r}"
            )

    if entry.get("period") and citation.claimed_period and citation.claimed_period != entry["period"]:
        problems.append(
            f"claimed_period {citation.claimed_period!r} does not match evidence_id "
            f"{citation.evidence_id!r}'s actual period {entry['period']!r}"
        )

    entry_kind = entry.get("value_kind")
    if (
        entry_kind not in (None, "unknown")
        and citation.claimed_value_kind not in (None, "unknown")
        and citation.claimed_value_kind != entry_kind
    ):
        problems.append(
            f"claimed_value_kind {citation.claimed_value_kind!r} does not match evidence_id "
            f"{citation.evidence_id!r}'s actual value_kind {entry_kind!r}"
        )

    return problems


def check_answer_grounding(draft: AnswerDraft, evidence: dict) -> dict:
    """Verify every number `draft.text` states is backed by a citation that
    is itself fully compatible with the evidence_id it names. Returns
    {"ok": bool, "ungrounded_numbers": [...], "citation_problems": {evidence_id: [...]}}.

    A number in `text` with no matching valid citation is "ungrounded"
    (fabricated, or simply never cited). A citation whose evidence_id
    exists but whose claimed geography/period/value_kind is wrong, or
    whose stated_value doesn't match the real value, is an "incompatible"
    citation — that specific citation doesn't count toward grounding any
    number, even if its evidence_id is real."""
    citation_problems: dict[str, list[str]] = {}
    valid: list[Citation] = []
    for citation in draft.citations:
        problems = check_citation(citation, evidence)
        if problems:
            citation_problems[citation.evidence_id] = problems
        else:
            valid.append(citation)

    valid_values = [v for c in valid if (v := _parse_stated_value(c.stated_value)) is not None]
    ungrounded = [
        value
        for value, decimals in extract_numbers(draft.text)
        if not any(_values_match(value, decimals, allowed_value) for allowed_value, _ in valid_values)
    ]

    return {
        "ok": not ungrounded and not citation_problems,
        "ungrounded_numbers": ungrounded,
        "citation_problems": citation_problems,
    }


def write_answer(evidence: dict, writer: LLMAnswerWriter) -> AnswerDraft:
    return writer.write(system=ANSWER_WRITER_SYSTEM_PROMPT, evidence=evidence)


@dataclass(frozen=True)
class AnswerWriteResult:
    text: str
    #: False when every LLM attempt produced an ungrounded number or an
    #: incompatible citation (or the writer call itself failed) and `text`
    #: is the deterministic fallback instead — task section 9: never
    #: silently return unsupported prose.
    llm_written: bool
    ungrounded_numbers: tuple[float, ...] = ()
    citation_problems: dict[str, list[str]] | None = None
    attempts: int = 0


def write_and_verify_answer(
    evidence: dict,
    writer: LLMAnswerWriter,
    *,
    fallback_text: str,
    max_attempts: int = 2,
) -> AnswerWriteResult:
    """Write the answer, check its grounding, and retry on an ungrounded
    number or incompatible citation — falling back to `fallback_text` (the
    deterministic table-derived answer every mode already builds, see
    agent/modes.py) rather than ever returning prose that failed the
    check."""
    last_ungrounded: tuple[float, ...] = ()
    last_problems: dict[str, list[str]] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            draft = write_answer(evidence, writer)
        except Exception:  # noqa: BLE001 - falls through to the deterministic fallback
            break
        check = check_answer_grounding(draft, evidence.get("evidence") or {})
        if check["ok"]:
            return AnswerWriteResult(text=draft.text, llm_written=True, attempts=attempt)
        last_ungrounded = tuple(check["ungrounded_numbers"])
        last_problems = check["citation_problems"] or None

    return AnswerWriteResult(
        text=fallback_text,
        llm_written=False,
        ungrounded_numbers=last_ungrounded,
        citation_problems=last_problems,
        attempts=max_attempts,
    )

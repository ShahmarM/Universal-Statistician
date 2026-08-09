"""Separate answer-writing pass + numeric consistency guard (Phase 6).

The investigator (agent/loop.py) never writes the user-facing answer —
its own final turn is debug-only (InvestigationState.investigator_summary,
see that module's docstring). This module is where prose actually gets
written, and it is deliberately constrained more tightly than the
investigator:

- Input is a plain JSON evidence package (InvestigationState.
  evidence_package()) — no tools, no message history, no ability to ask
  for more data. The writer cannot retrieve, calculate, or investigate;
  it can only describe what's already there.
- Output is checked, not trusted: `check_answer_numbers()` extracts every
  number the LLM wrote and confirms each one is actually present in the
  evidence (within a rounding tolerance — a model reporting "78.8%" for a
  stored 78.83 is legitimate rounding, not a hallucination). Any number
  that doesn't trace back to the evidence fails the check.
- `write_and_verify_answer()` retries a bounded number of times on
  failure, then falls back to a supplied deterministic answer instead of
  ever returning unchecked prose — "do not silently return unsupported
  prose" (task section 9) is enforced here, not left to the prompt alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from universal_statistician.agent.llm import LLMAnswerWriter

ANSWER_WRITER_SYSTEM_PROMPT = (
    "You write the final answer to a statistical question from ONLY the "
    "validated evidence provided below, as JSON. You have no tools and cannot "
    "look anything up — everything you say must come from this evidence.\n\n"
    "Mandatory rules:\n"
    "- Use only the numbers present in the evidence (in `table`, "
    "`provenance_references`, or `result_ids`). Never estimate, compute a new "
    "figure, or state a number that isn't there.\n"
    "- Never invent a missing period, country, or value. If the evidence "
    "doesn't cover something the question asks about, say so plainly instead "
    "of filling the gap.\n"
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

#: Matches a numeric token, optionally with proper thousands-grouped commas
#: (groups of exactly 3 digits — so a sentence comma like "In 2022, GDP..."
#: never gets swallowed into the number), a decimal part, and a trailing
#: percent sign — e.g. "78.8", "10,400,000", "-3.2%".
_NUMBER_PATTERN = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?")

#: A bare 4-digit integer in this range is treated as a period/year
#: reference, not a statistic requiring evidence support — "in 2023" isn't
#: a hallucinated number the same way a wrong GDP figure would be.
_YEAR_RANGE = range(1900, 2101)


def extract_numbers(text: str) -> list[tuple[float, int]]:
    """Every numeric token in `text`, as (value, decimal_places) — the
    decimal count is kept so the consistency check can apply a rounding
    tolerance matched to how precisely the model actually wrote the
    number, rather than requiring an exact float match no prose would
    ever produce."""
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


def collect_allowed_numbers(evidence: dict) -> list[float]:
    """Every numeric value actually present in the evidence package — the
    only numbers an answer is allowed to state."""
    allowed: list[float] = []
    table = evidence.get("table") or {}
    for row in table.get("rows", []) or []:
        for key, value in row.items():
            if key == "period":
                continue
            if isinstance(value, (int, float)):
                allowed.append(float(value))

    def _collect_provenance(node: dict) -> None:
        value = node.get("value")
        if isinstance(value, (int, float)):
            allowed.append(float(value))
        for child in node.get("inputs", []) or []:
            _collect_provenance(child)

    for reference in evidence.get("provenance_references", []) or []:
        _collect_provenance(reference)

    return allowed


def _matches_any(value: float, decimals: int, allowed: list[float]) -> bool:
    decimals = max(0, min(decimals, 6))
    target = round(value, decimals)
    return any(round(a, decimals) == target for a in allowed)


def check_answer_numbers(text: str, evidence: dict) -> dict:
    """Verify every number in `text` traces back to the evidence package.
    Returns {"ok": bool, "unsupported_numbers": [...]}."""
    allowed = collect_allowed_numbers(evidence)
    unsupported = [
        value for value, decimals in extract_numbers(text) if not _matches_any(value, decimals, allowed)
    ]
    return {"ok": not unsupported, "unsupported_numbers": unsupported}


def write_answer(evidence: dict, writer: LLMAnswerWriter) -> str:
    return writer.write(system=ANSWER_WRITER_SYSTEM_PROMPT, evidence=evidence)


@dataclass(frozen=True)
class AnswerWriteResult:
    text: str
    #: False when every LLM attempt produced an unsupported number (or the
    #: writer call itself failed) and `text` is the deterministic fallback
    #: instead — task section 9: never silently return unsupported prose.
    llm_written: bool
    unsupported_numbers: tuple[float, ...] = ()
    attempts: int = 0


def write_and_verify_answer(
    evidence: dict,
    writer: LLMAnswerWriter,
    *,
    fallback_text: str,
    max_attempts: int = 2,
) -> AnswerWriteResult:
    """Write the answer, check it, and retry on an unsupported number —
    falling back to `fallback_text` (the deterministic table-derived
    answer every mode already builds, see agent/modes.py) rather than
    ever returning prose that failed the check."""
    last_unsupported: tuple[float, ...] = ()
    for attempt in range(1, max_attempts + 1):
        try:
            text = write_answer(evidence, writer)
        except Exception:  # noqa: BLE001 - falls through to the deterministic fallback
            break
        check = check_answer_numbers(text, evidence)
        if check["ok"]:
            return AnswerWriteResult(text=text, llm_written=True, attempts=attempt)
        last_unsupported = tuple(check["unsupported_numbers"])

    return AnswerWriteResult(
        text=fallback_text,
        llm_written=False,
        unsupported_numbers=last_unsupported,
        attempts=max_attempts,
    )

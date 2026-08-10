"""Independent LLM verification pass over a draft answer.

Complements answer_writer.py's code-only citation check by reading the
draft's *prose* against the evidence for semantic errors a citation-ID
check can't see (prose disagreeing with its own citations, nominal vs.
real compared silently, a FAILED validation presented as clean). No
tools, no history; the verdict is a forced tool call with a closed
status/category enum agent/modes.py's bounded retry loop reasons about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-5"

VERIFY_TOOL_NAME = "report_verification"

#: Closed set of semantic error classes — programmatically reasoned about,
#: never freeform text.
ISSUE_CATEGORIES = (
    "contradicts_evidence",
    "geography_mismatch",
    "ignored_validation_failure",
    "ignored_warning",
    "missing_source_attribution",
    "period_mismatch",
    "price_basis_mismatch",
    "unit_mismatch",
    "unsupported_claim",
    "unsupported_number",
)

VERIFIER_SYSTEM_PROMPT = (
    "You verify a draft statistical answer against the evidence it was "
    "supposedly built from. You have no tools and cannot look anything up "
    "beyond what's given below.\n\n"
    "Check specifically for:\n"
    "- unsupported_number: a number in the draft that isn't backed by any "
    "cell in the evidence's `evidence` object (evidence_id -> {value, unit, "
    "value_kind, geography, period, ...}) or `table`, even allowing for "
    "reasonable rounding.\n"
    "- price_basis_mismatch: comparing or combining nominal (current-price) "
    "and real (constant-price) figures without disclosing it.\n"
    "- unit_mismatch: combining or comparing values in different units "
    "without conversion or disclosure.\n"
    "- geography_mismatch: a value attributed to the wrong country/area, or "
    "a country mentioned that has no data in the evidence.\n"
    "- period_mismatch: a period mentioned that has no data in the evidence, "
    "or values from different periods presented as directly comparable "
    "without saying so.\n"
    "- ignored_validation_failure: the evidence's validation_results show "
    "WARNING or FAIL, but the draft presents the result as clean and fully "
    "supported.\n"
    "- ignored_warning: the evidence's warnings list contains something "
    "material (e.g. a missing geography, a failed retrieval) the draft "
    "omits entirely.\n"
    "- unsupported_claim: a causal or explanatory claim (e.g. 'X caused Y') "
    "the evidence doesn't actually state or support.\n"
    "- missing_source_attribution: the evidence involves more than one "
    "source and the draft doesn't distinguish which figure came from where.\n"
    "- contradicts_evidence: the draft states something that directly "
    "conflicts with a value or fact in the evidence.\n\n"
    "Set status=PASS only if you find no issues. Use WARNING for issues that "
    "don't invalidate the answer's core claim (e.g. a minor omitted "
    "caveat). Use FAIL for anything that makes the answer numerically wrong "
    "or unsupported. Always report every issue you find, even under "
    "WARNING — never suppress one because the overall status is otherwise "
    "acceptable."
)

VERIFY_TOOL_SCHEMA = {
    "name": VERIFY_TOOL_NAME,
    "description": (
        "Report the result of verifying a draft answer against the evidence "
        "it was supposedly built from."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "WARNING", "FAIL"]},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(ISSUE_CATEGORIES)},
                        "detail": {
                            "type": "string",
                            "description": "Plain-language explanation of this specific issue.",
                        },
                    },
                    "required": ["category", "detail"],
                },
                "description": "Every issue found, even ones that only warrant WARNING.",
            },
        },
        "required": ["status", "issues"],
    },
}


@dataclass(frozen=True)
class VerificationIssue:
    category: str
    detail: str

    def as_dict(self) -> dict:
        return {"category": self.category, "detail": self.detail}


@dataclass(frozen=True)
class VerificationReport:
    status: str  # "PASS" | "WARNING" | "FAIL"
    issues: tuple[VerificationIssue, ...] = ()

    def as_dict(self) -> dict:
        return {"status": self.status, "issues": [issue.as_dict() for issue in self.issues]}

    @staticmethod
    def from_tool_input(payload: dict) -> "VerificationReport":
        status = str(payload.get("status") or "").strip().upper()
        if status not in ("PASS", "WARNING", "FAIL"):
            # An unparseable verdict is never silently treated as a pass.
            status = "FAIL"
        issues = []
        for raw in payload.get("issues") or []:
            if not isinstance(raw, dict):
                continue
            category = str(raw.get("category") or "").strip()
            if category not in ISSUE_CATEGORIES:
                category = "unsupported_claim"
            issues.append(VerificationIssue(category=category, detail=str(raw.get("detail") or "")))
        return VerificationReport(status=status, issues=tuple(issues))


@runtime_checkable
class LLMVerifier(Protocol):
    def verify(self, *, question: str, evidence: dict, draft_answer: str) -> VerificationReport:
        """Check `draft_answer` against `evidence` only; returns a
        structured verdict, never free text."""
        ...


@dataclass
class AnthropicVerifier:
    """LLMVerifier via a forced tool call — the model can only fill in
    VERIFY_TOOL_SCHEMA. `client` is injected for testability."""

    client: Any
    model: str = DEFAULT_MODEL
    max_tokens: int = 1024

    def verify(self, *, question: str, evidence: dict, draft_answer: str) -> VerificationReport:
        payload = {"question": question, "evidence": evidence, "draft_answer": draft_answer}
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=VERIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            tools=[VERIFY_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": VERIFY_TOOL_NAME},
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        return VerificationReport.from_tool_input(tool_use.input)

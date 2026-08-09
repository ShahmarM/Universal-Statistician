"""Vendor-neutral LLM role abstractions (task section 22).

Three distinct roles get three distinct Protocols across this migration's
phases — never one interface named after a vendor, and never a Protocol
wide enough to let one role quietly do another's job (the investigator
must not write final prose, the answer writer must not call tools, the
verifier must not touch data):

- `LLMAgent` (Phase 2) — the iterative investigator
  (agent/loop.py's `StatisticalAgent`): one tool-calling turn at a time.
- `LLMAnswerWriter` (this phase) — writes prose from already-validated
  evidence only, no tool access.
- `LLMVerifier` (Phase 7) — checks a draft answer against the evidence,
  no tool access, cannot modify data.

Each Protocol's shape mirrors the already-established convention in this
project (planning/anthropic_planner.py, chat.py): a client is injected,
never constructed internally, so every role is testable with a fake
object built from real `anthropic.types` shapes — no network, no API key,
no vendor SDK import at module load time.

Only one vendor is implemented (Anthropic) — task section 22: "one
working provider plus clean abstraction is enough." A later `OpenAI*`/
`Local*` adapter would implement the same Protocols without touching
agent/loop.py, agent/answer_writer.py, or agent/verifier.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-5"


@runtime_checkable
class LLMAgent(Protocol):
    def step(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """One investigator turn. Returns an Anthropic-Message-shaped object:
        `.content` is a list of blocks, each with `.type` ('text' |
        'tool_use'), and for a tool_use block `.id`/`.name`/`.input`, for a
        text block `.text`. agent/loop.py appends this object's `.content`
        verbatim into the next turn's messages, the same pattern chat.py's
        ChatSession already uses — so any object with this shape works,
        real SDK response or test double alike."""
        ...


@dataclass
class AnthropicAgent:
    """LLMAgent backed by the Claude API. `client` is injected (never
    constructed here) — same testability convention as
    planning/anthropic_planner.py::AnthropicPlanner and chat.py::ChatSession."""

    client: Any
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096

    def step(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


@runtime_checkable
class LLMAnswerWriter(Protocol):
    def write(self, *, system: str, evidence: dict) -> str:
        """Produce prose from a JSON-serializable evidence package only —
        no tools, no message history, no ability to ask a follow-up
        question. See agent/answer_writer.py (Phase 6) for what `evidence`
        contains and how the result is checked for unsupported numbers
        before being trusted."""
        ...


@dataclass
class AnthropicAnswerWriter:
    """LLMAnswerWriter backed by the Claude API — a single, tool-free call:
    system instruction plus the evidence package as the only user turn.
    Deliberately no `tools=` argument at all, unlike AnthropicAgent — this
    role cannot call anything, by construction, not just by prompt."""

    client: Any
    model: str = DEFAULT_MODEL
    max_tokens: int = 2048

    def write(self, *, system: str, evidence: dict) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": json.dumps(evidence, default=str)}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

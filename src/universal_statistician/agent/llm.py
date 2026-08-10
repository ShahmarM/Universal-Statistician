"""Vendor-neutral LLM role Protocols.

Each role (investigator here; answer writer and verifier in their own
self-contained modules, which avoids a circular import) gets a narrow
Protocol so no role can quietly do another's job. Clients are injected,
never constructed, so every role is testable offline. Only Anthropic is
implemented; another vendor would implement the same Protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-5"


@runtime_checkable
class LLMAgent(Protocol):
    def step(self, *, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """One investigator turn. Returns an Anthropic-Message-shaped
        object (`.content` blocks with `.type`, and `.id`/`.name`/`.input`
        or `.text`); real SDK response or test double alike."""
        ...


@dataclass
class AnthropicAgent:
    """LLMAgent backed by the Claude API; `client` is injected for
    testability."""

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



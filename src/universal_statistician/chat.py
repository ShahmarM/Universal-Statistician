"""Chat over the same tools the MCP server exposes, via Claude API tool use.

Reuses mcp_server.server for both schemas and execution rather than
describing or dispatching them a second time.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

from universal_statistician.mcp_server import server as _mcp_server

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
SYSTEM_PROMPT = (
    "You are a research assistant with access to official statistics tools "
    "(World Bank, IMF, Eurostat, Statistics Sweden). Every number you report "
    "must come from a tool call — never state a statistic from memory. "
    "When you report a value, mention its source (the tool result's "
    "attribution: source name, dataset id, retrieval time)."
)


def get_tool_schemas() -> list[dict]:
    """Anthropic `tools` list, built from the MCP server's own definitions."""
    mcp_tools = asyncio.run(_mcp_server.list_tools())
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in mcp_tools
    ]


def execute_tool_call(name: str, arguments: dict) -> tuple[str, bool]:
    """Run one tool call through the MCP server's call_tool() and return
    (text, is_error) for a tool_result. Anything raised becomes an error
    result the model can react to, rather than crashing the loop."""
    try:
        result = asyncio.run(_mcp_server.call_tool(name, arguments))
    except Exception as exc:
        return str(exc), True
    text = result.content[0].text if result.content else ""
    return text, bool(result.is_error)


@dataclass
class ChatSession:
    """The conversation loop. `client` needs only `.messages.create(...)`
    returning the SDK's Message shape, so tests can inject a fake."""

    client: Any
    model: str = DEFAULT_MODEL
    tools: list[dict] = field(default_factory=get_tool_schemas)
    messages: list[dict] = field(default_factory=list)

    def send(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=self.messages,
                tools=self.tools,
            )
            self.messages.append({"role": "assistant", "content": response.content})

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                return "".join(b.text for b in response.content if b.type == "text")

            tool_results = []
            for block in tool_use_blocks:
                text, is_error = execute_tool_call(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )
            self.messages.append({"role": "user", "content": tool_results})


def run_chat(model: str = DEFAULT_MODEL) -> None:
    """Thin stdin/stdout REPL over ChatSession; the testable logic lives in
    ChatSession and execute_tool_call."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Get a key at "
            "https://console.anthropic.com/ and export it, then retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from anthropic import Anthropic

    session = ChatSession(client=Anthropic(), model=model)
    print(f"Universal Statistician chat ({model}). Ctrl+D or 'exit' to quit.")
    while True:
        try:
            user_text = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_text.strip().lower() in {"exit", "quit"}:
            break
        if not user_text.strip():
            continue
        print(session.send(user_text))

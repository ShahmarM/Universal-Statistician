"""Chat over the same tools the MCP server exposes, via Claude API tool use.

Reuses mcp_server.server for both tool schemas (list_tools) and tool
execution (call_tool) rather than describing or dispatching them a second
time — the same "define once, reuse across interfaces" principle already
applied to tools.py, just extended one level further to cover the tool
*schemas* too, not only their logic.
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
    """Anthropic `tools` list, built from the MCP server's own tool
    definitions rather than a second hand-written copy."""
    mcp_tools = asyncio.run(_mcp_server.list_tools())
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in mcp_tools
    ]


def execute_tool_call(name: str, arguments: dict) -> tuple[str, bool]:
    """Run one tool call through the MCP server's real call_tool(), and
    return (text, is_error) ready to feed back to Claude as a tool_result.

    Anything call_tool() raises (ToolError on bad input, or an unhandled
    provider exception) becomes an error tool_result instead of crashing the
    chat loop — Claude can see the failure and decide how to respond, the
    same way a human would read an error message rather than the process
    dying.
    """
    try:
        result = asyncio.run(_mcp_server.call_tool(name, arguments))
    except Exception as exc:
        return str(exc), True
    text = result.content[0].text if result.content else ""
    return text, bool(result.is_error)


@dataclass
class ChatSession:
    """The conversation loop, independent of any specific Anthropic client
    instance — `client` only needs a `.messages.create(...)` method
    returning something with the real SDK's `Message` shape (`.content`
    list of blocks with `.type`/`.text`/`.name`/`.input`/`.id`), so tests can
    inject a fake without touching the network or needing an API key."""

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
    """Thin REPL: stdin/stdout loop over ChatSession. Not unit-tested, same
    as cli.py/mcp_server.py not testing their own process entry points —
    the logic worth testing lives in ChatSession and execute_tool_call."""
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

"""chat.py tests.

get_tool_schemas()/execute_tool_call() are exercised against the real
mcp_server.server (same network-free tools already proven safe in
test_mcp_server.py: list_sources, search_indicator, describe_source).
ChatSession's loop is tested against a fake client built from *real*
anthropic.types.Message/TextBlock/ToolUseBlock/Usage objects (ground truth
for the response shape), not a hand-guessed dict — there is no
ANTHROPIC_API_KEY in this sandbox, so a live conversation genuinely cannot
be exercised here; this is the honest ceiling of what's testable offline.
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from universal_statistician.chat import ChatSession, execute_tool_call, get_tool_schemas


def _message(*blocks, stop_reason="end_turn") -> Message:
    return Message(
        id="msg_test",
        model="claude-sonnet-5",
        role="assistant",
        type="message",
        stop_reason=stop_reason,
        stop_sequence=None,
        content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class FakeClient:
    """Returns each scripted Message in order, one per call to .create()."""

    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


# ---- get_tool_schemas / execute_tool_call (real MCP server) ---------------


def test_get_tool_schemas_matches_the_mcp_server():
    schemas = get_tool_schemas()
    names = {s["name"] for s in schemas}
    assert names == {
        "search_indicator",
        "get_series",
        "compare",
        "list_sources",
        "describe_source",
        "ask",
    }
    for schema in schemas:
        assert schema["description"]
        assert isinstance(schema["input_schema"], dict)


def test_execute_tool_call_success_for_network_free_tool():
    text, is_error = execute_tool_call("list_sources", {})
    assert is_error is False
    assert "WB_WDI" in text


def test_execute_tool_call_reports_unknown_source_as_error():
    text, is_error = execute_tool_call("describe_source", {"source_id": "NOPE"})
    assert is_error is True
    assert "Unknown source" in text


# ---- ChatSession ------------------------------------------------------------


def test_send_returns_text_when_claude_calls_no_tool():
    client = FakeClient([_message(TextBlock(type="text", text="Hello there."))])
    session = ChatSession(client=client, tools=[])

    reply = session.send("hi")

    assert reply == "Hello there."
    assert len(client.calls) == 1
    assert session.messages[0] == {"role": "user", "content": "hi"}


def test_send_executes_a_tool_call_and_returns_the_final_text():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="list_sources", input={}),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="World Bank is among the sources.")),
        ]
    )
    session = ChatSession(client=client, tools=get_tool_schemas())

    reply = session.send("what sources are there?")

    assert reply == "World Bank is among the sources."
    assert len(client.calls) == 2

    # user, assistant(tool_use), user(tool_result), assistant(text)
    assert len(session.messages) == 4
    tool_result_message = session.messages[2]
    assert tool_result_message["role"] == "user"
    tool_result = tool_result_message["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["is_error"] is False
    assert "WB_WDI" in tool_result["content"]


def test_send_feeds_back_a_failed_tool_call_as_an_error_result_without_raising():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(
                    type="tool_use",
                    id="tu_1",
                    name="describe_source",
                    input={"source_id": "NOPE"},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="That source doesn't exist.")),
        ]
    )
    session = ChatSession(client=client, tools=get_tool_schemas())

    reply = session.send("describe NOPE")

    assert reply == "That source doesn't exist."
    tool_result = session.messages[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "Unknown source" in tool_result["content"]


def test_send_handles_multiple_tool_use_blocks_in_one_response():
    client = FakeClient(
        [
            _message(
                ToolUseBlock(type="tool_use", id="tu_1", name="list_sources", input={}),
                ToolUseBlock(
                    type="tool_use",
                    id="tu_2",
                    name="search_indicator",
                    input={"query": "population"},
                ),
                stop_reason="tool_use",
            ),
            _message(TextBlock(type="text", text="Done.")),
        ]
    )
    session = ChatSession(client=client, tools=get_tool_schemas())

    reply = session.send("look things up")

    assert reply == "Done."
    tool_results = session.messages[2]["content"]
    assert [r["tool_use_id"] for r in tool_results] == ["tu_1", "tu_2"]
    assert all(r["is_error"] is False for r in tool_results)

"""Exercises the actual MCP protocol path (server.call_tool), not just the
tools.py functions it wraps. This is deliberate: the cross-thread sqlite3 bug
fixed in Catalog/Cache was invisible to tools.py-level tests and only showed
up here, because MCPServer runs each tool in a worker thread. Every test
below is restricted to tools that don't need live network (search_indicator,
list_sources, describe_source) — get_series/compare against a real source are
exercised at the tools.py level with a fake provider instead.

Reading results is more subtle than it looks (confirmed empirically, not
assumed): a list-returning tool gets structured_content={"result": [...]}
*and* one separate TextContent item per list element (not one combined JSON
array), while a bare `-> dict` return gets no structured_content at all and
exactly one TextContent with the JSON object. `call()` below normalizes
both cases.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from universal_statistician.mcp_server import server


def call(name: str, arguments: dict):
    result = asyncio.run(server.call_tool(name, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


def test_all_five_tools_are_registered():
    tool_list = asyncio.run(server.list_tools())
    assert {t.name for t in tool_list} == {
        "search_indicator",
        "get_series",
        "compare",
        "list_sources",
        "describe_source",
    }


def test_list_sources_tool_call_succeeds():
    source_ids = {row["source_id"] for row in call("list_sources", {})}
    assert {"WB_WDI", "IMF_DATA", "ESTAT"} <= source_ids


def test_describe_source_tool_call_succeeds():
    assert call("describe_source", {"source_id": "WB_WDI"})["source_id"] == "WB_WDI"


def test_describe_source_unknown_id_raises_tool_error():
    with pytest.raises(ToolError, match="Unknown source"):
        call("describe_source", {"source_id": "NOPE"})


def test_search_indicator_tool_call_runs_in_a_worker_thread_successfully():
    # Regression coverage for the Catalog cross-thread sqlite3 bug: this call
    # only succeeds if Catalog.search() tolerates running outside the thread
    # that constructed it.
    results = call("search_indicator", {"query": "population"})
    assert any(row["indicator_id"] == "SP_POP_TOTL" for row in results)

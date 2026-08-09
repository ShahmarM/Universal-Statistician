"""MCP server exposing the query engine as tools for an LLM host.

Deliberately thin: every tool here is a one-line call into universal_statistician.tools,
which is where the actual dispatch logic lives (and is unit-tested without any
MCP protocol machinery). Understanding the user's natural-language request
into these structured calls is the connected LLM host's job, not this
server's — see plan.md on why we don't build our own NLU.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from universal_statistician import tools
from universal_statistician.core.engine import QueryEngine, default_engine

server = MCPServer(
    name="universal-statistician",
    title="Universal Statistician",
    description=(
        "Normalized, source-attributed access to official statistics "
        "(World Bank, IMF, Eurostat, ...), including cross-country and "
        "cross-indicator comparison tables."
    ),
)

_engine: QueryEngine = default_engine()


@server.tool()
def search_indicator(query: str, limit: int = 20) -> list[dict]:
    """Search the indicator catalog by name, in any indexed language.
    Returns indicator_id/source_id pairs to pass to get_series or compare."""
    return tools.search_indicator(_engine, query, limit=limit)


@server.tool()
def get_series(
    source_id: str,
    indicator_id: str,
    ref_area: str,
    start_period: str | None = None,
    end_period: str | None = None,
) -> dict:
    """Fetch one indicator's time series for one area/country. Every result
    carries an attribution: source, dataset id, retrieval time, source URL."""
    return tools.get_series(
        _engine, source_id, indicator_id, ref_area, start_period, end_period
    )


@server.tool()
def compare(
    source_id: str,
    indicator_id: str | None = None,
    ref_areas: list[str] | None = None,
    indicator_ids: list[str] | None = None,
    ref_area: str | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
    growth: bool = False,
    rank: bool = False,
    ratio_to: str | None = None,
) -> dict:
    """Build a comparison table. Pass indicator_id + ref_areas for a
    cross-country comparison of one indicator (e.g. GDP across countries), or
    indicator_ids + ref_area for a cross-indicator comparison for one country
    (e.g. GDP vs unemployment). Set growth/rank/ratio_to to add computed
    columns (year-over-year %, rank per period, ratio to a baseline area or
    indicator) — these are computed here, not reported by the source, and are
    marked as such in the response."""
    return tools.compare(
        _engine,
        source_id,
        indicator_id=indicator_id,
        ref_areas=ref_areas,
        indicator_ids=indicator_ids,
        ref_area=ref_area,
        start_period=start_period,
        end_period=end_period,
        growth=growth,
        rank=rank,
        ratio_to=ratio_to,
    )


@server.tool()
def list_sources() -> list[dict]:
    """List every registered data source (id, name, dataflow, website)."""
    return tools.list_sources(_engine)


@server.tool()
def describe_source(source_id: str) -> dict:
    """Describe one registered data source."""
    return tools.describe_source(_engine, source_id)


@server.tool()
def ask(question: str) -> dict:
    """Full pipeline for a literal catalog search phrase: resolve it against
    the indicator catalog, retrieve official observations, apply any
    requested computation, validate, and return an answer with citations
    and provenance — one call instead of composing search_indicator +
    get_series/compare yourself. Uses a deterministic, non-LLM interpreter
    for the phrase (this server assumes the connected host already did
    natural-language understanding to produce `question`); prefer
    search_indicator + get_series/compare directly for anything requiring
    real NL interpretation beyond a literal search phrase."""
    return tools.ask(_engine, question)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()

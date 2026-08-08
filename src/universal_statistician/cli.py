"""CLI over the same core engine — for development and smoke-testing without
an MCP client. Thin like mcp_server.py: every command is a call into
universal_statistician.tools, printed as JSON. No separate output formatting
logic to keep in sync with the MCP server's response shape.
"""

from __future__ import annotations

import json
from typing import Optional

import typer

from universal_statistician import tools
from universal_statistician.core.engine import UnknownSourceError, default_engine

app = typer.Typer(add_completion=False, help="Universal Statistician CLI (dev/smoke-test tool).")
_engine = default_engine()


def _print(payload) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _run(fn, *args, **kwargs) -> None:
    """Call an operation that can fail on bad user input (unknown source,
    malformed compare() shape) and print a clean message instead of a raw
    traceback — everything else is a real bug and should still surface."""
    try:
        _print(fn(*args, **kwargs))
    except (ValueError, UnknownSourceError) as exc:
        # UnknownSourceError subclasses KeyError, whose __str__ wraps the
        # message in an extra layer of repr-quoting — undo that for display.
        message = exc.args[0] if exc.args else str(exc)
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def search(query: str, limit: int = 20) -> None:
    """Search the indicator catalog by name, in any indexed language."""
    _run(tools.search_indicator, _engine, query, limit=limit)


@app.command()
def series(
    source_id: str,
    indicator_id: str,
    ref_area: str,
    start: Optional[str] = typer.Option(None, help="start_period, e.g. 2015"),
    end: Optional[str] = typer.Option(None, help="end_period, e.g. 2020"),
) -> None:
    """Fetch one indicator's time series for one area/country."""
    _run(tools.get_series, _engine, source_id, indicator_id, ref_area, start, end)


@app.command()
def compare(
    source_id: str,
    indicator: Optional[str] = typer.Option(
        None, "--indicator", help="One indicator, for a cross-country comparison"
    ),
    ref_areas: Optional[list[str]] = typer.Option(
        None, "--ref-area", help="Repeatable: --ref-area AFG --ref-area USA"
    ),
    indicators: Optional[list[str]] = typer.Option(
        None, "--indicator-id", help="Repeatable, for a cross-indicator comparison"
    ),
    ref_area: Optional[str] = typer.Option(
        None, "--for-area", help="One area, for a cross-indicator comparison"
    ),
    start: Optional[str] = typer.Option(None, help="start_period"),
    end: Optional[str] = typer.Option(None, help="end_period"),
    growth: bool = typer.Option(False, help="Add year-over-year %% growth columns"),
    rank: bool = typer.Option(False, help="Add per-period rank columns"),
    ratio_to: Optional[str] = typer.Option(None, help="Add ratio-to-baseline columns"),
) -> None:
    """Cross-country comparison (--indicator + --ref-area ...) or
    cross-indicator comparison (--indicator-id ... + --for-area)."""
    _run(
        tools.compare,
        _engine,
        source_id,
        indicator_id=indicator,
        ref_areas=ref_areas,
        indicator_ids=indicators,
        ref_area=ref_area,
        start_period=start,
        end_period=end,
        growth=growth,
        rank=rank,
        ratio_to=ratio_to,
    )


@app.command("sources")
def list_sources_cmd() -> None:
    """List every registered data source."""
    _run(tools.list_sources, _engine)


@app.command("source")
def describe_source_cmd(source_id: str) -> None:
    """Describe one registered data source."""
    _run(tools.describe_source, _engine, source_id)


if __name__ == "__main__":
    app()

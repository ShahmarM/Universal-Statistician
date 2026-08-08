"""FastAPI REST layer over the same core engine — a third thin interface next
to mcp_server.py and cli.py, same shape: every endpoint is a call into
universal_statistician.tools, no business logic of its own. Auto-generated
OpenAPI docs (FastAPI's default /docs) double as a contract for a future
dashboard, per plan.md's step 9.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from universal_statistician import tools
from universal_statistician.core.engine import QueryEngine, UnknownSourceError, default_engine

app = FastAPI(
    title="Universal Statistician",
    description=(
        "Normalized, source-attributed access to official statistics "
        "(World Bank, IMF, Eurostat, ...), including cross-country and "
        "cross-indicator comparison tables."
    ),
)

_engine: QueryEngine = default_engine()


def _call(fn, *args, **kwargs):
    """Run a tools.py call and translate its two user-input error types into
    HTTP responses instead of a raw 500 — same principle as cli.py's _run."""
    try:
        return fn(*args, **kwargs)
    except UnknownSourceError as exc:
        # UnknownSourceError subclasses KeyError, whose __str__ double-quotes
        # the message — unwrap args[0] as cli.py's _run() does.
        detail = exc.args[0] if exc.args else str(exc)
        raise HTTPException(status_code=404, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sources")
def list_sources() -> list[dict]:
    return _call(tools.list_sources, _engine)


@app.get("/sources/{source_id}")
def describe_source(source_id: str) -> dict:
    return _call(tools.describe_source, _engine, source_id)


@app.get("/search")
def search_indicator(q: str, limit: int = 20) -> list[dict]:
    return _call(tools.search_indicator, _engine, q, limit=limit)


@app.get("/series")
def get_series(
    source_id: str,
    indicator_id: str,
    ref_area: str,
    start_period: Optional[str] = None,
    end_period: Optional[str] = None,
) -> dict:
    return _call(
        tools.get_series, _engine, source_id, indicator_id, ref_area, start_period, end_period
    )


class CompareRequest(BaseModel):
    source_id: str
    indicator_id: Optional[str] = None
    ref_areas: Optional[list[str]] = None
    indicator_ids: Optional[list[str]] = None
    ref_area: Optional[str] = None
    start_period: Optional[str] = None
    end_period: Optional[str] = None
    growth: bool = False
    rank: bool = False
    ratio_to: Optional[str] = None


@app.post("/compare")
def compare(body: CompareRequest) -> dict:
    return _call(
        tools.compare,
        _engine,
        body.source_id,
        indicator_id=body.indicator_id,
        ref_areas=body.ref_areas,
        indicator_ids=body.indicator_ids,
        ref_area=body.ref_area,
        start_period=body.start_period,
        end_period=body.end_period,
        growth=body.growth,
        rank=body.rank,
        ratio_to=body.ratio_to,
    )

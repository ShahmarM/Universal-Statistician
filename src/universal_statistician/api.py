"""FastAPI REST layer over the same core engine — a third thin interface next
to mcp_server.py and cli.py, same shape: every endpoint is a call into
universal_statistician.tools, no business logic of its own. Auto-generated
OpenAPI docs (FastAPI's default /docs) double as a contract for a future
dashboard, per plan.md's step 9.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from universal_statistician import tools
from universal_statistician.core.engine import QueryEngine, UnknownSourceError, default_engine
from universal_statistician.planning.base import LLMPlanner

app = FastAPI(
    title="Universal Statistician",
    description=(
        "Normalized, source-attributed access to official statistics "
        "(World Bank, IMF, Eurostat, ...), including cross-country and "
        "cross-indicator comparison tables."
    ),
)


def _cors_kwargs() -> dict:
    """Origins from USTAT_CORS_ORIGINS (comma-separated); unset allows any
    localhost/127.0.0.1 origin — never widened to "everything" implicitly."""
    origins = os.environ.get("USTAT_CORS_ORIGINS")
    if origins:
        return {"allow_origins": [o.strip() for o in origins.split(",") if o.strip()]}
    return {"allow_origin_regex": r"http://(localhost|127\.0\.0\.1)(:\d+)?"}


app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **_cors_kwargs(),
)


class _RateLimiter:
    """Fixed-window counter per client IP, in-process only — a
    multi-process deployment would need a shared backend."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, float]] = {}

    def allow(self, key: str) -> bool:
        if self._max_requests <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            count, window_start = self._counts.get(key, (0, now))
            if now - window_start >= self._window_seconds:
                count, window_start = 0, now
            count += 1
            self._counts[key] = (count, window_start)
            return count <= self._max_requests


#: USTAT_RATE_LIMIT_REQUESTS=0 (the default) disables rate limiting.
_rate_limiter = _RateLimiter(
    max_requests=int(os.environ.get("USTAT_RATE_LIMIT_REQUESTS", "0")),
    window_seconds=float(os.environ.get("USTAT_RATE_LIMIT_WINDOW_SECONDS", "60")),
)


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(client):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    return await call_next(request)


_engine: QueryEngine = default_engine()


def _call(fn, *args, **kwargs):
    """Translate errors into HTTP responses. The broad `except Exception`
    is load-bearing: an escaped provider exception aborts the response
    before CORSMiddleware attaches headers, so the browser misreports it
    as a CORS failure instead of a 502."""
    try:
        return fn(*args, **kwargs)
    except UnknownSourceError as exc:
        # KeyError's __str__ double-quotes the message; unwrap args[0].
        detail = exc.args[0] if exc.args else str(exc)
        raise HTTPException(status_code=404, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Upstream data source request failed: {exc}"
        ) from exc


@app.get("/health")
def health() -> dict:
    """Liveness check touching only the local catalog — a probe must work
    even when every upstream statistics API is unreachable."""
    catalog = _engine.catalog_stats()
    return {"status": "ok", "catalog": catalog}


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


class QuestionRequest(BaseModel):
    question: str
    #: Opt into the server's configured Anthropic-backed LLM roles. Never
    #: accepts a key in the request body — boolean opt-in only.
    use_llm: bool = False
    #: "auto" | "fast" | "research"; explicit override beats auto's
    #: heuristic. /plan ignores this.
    mode: str = "auto"
    #: Include the investigation audit trail under "debug" (research mode
    #: only). No hidden chain-of-thought exists to expose.
    debug: bool = False


def _resolve_planner(use_llm: bool) -> Optional[LLMPlanner]:
    if not use_llm:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="use_llm=true requires ANTHROPIC_API_KEY to be set in the server's environment.",
        )
    from anthropic import Anthropic

    from universal_statistician.planning.anthropic_planner import AnthropicPlanner

    return AnthropicPlanner(client=Anthropic())


def _resolve_agent_components(use_llm: bool):
    """The three agent-mode LLM roles sharing one client; None means "run
    without an LLM" (modes.py falls back to fast mode with a warning)."""
    if not use_llm:
        return None, None, None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="use_llm=true requires ANTHROPIC_API_KEY to be set in the server's environment.",
        )
    from anthropic import Anthropic

    from universal_statistician.agent.answer_writer import AnthropicAnswerWriter
    from universal_statistician.agent.llm import AnthropicAgent
    from universal_statistician.agent.verifier import AnthropicVerifier

    client = Anthropic()
    return AnthropicAgent(client=client), AnthropicAnswerWriter(client=client), AnthropicVerifier(client=client)


@app.post("/plan")
def plan(body: QuestionRequest) -> dict:
    """Structured query plan only — no retrieval. Debug/inspection
    endpoint."""
    planner = _resolve_planner(body.use_llm)
    return _call(tools.build_plan, _engine, body.question, planner)


@app.post("/ask")
def ask(body: QuestionRequest) -> dict:
    """Full pipeline: mode selection -> investigation/plan -> retrieval ->
    transformations -> validation -> answer + table + chart + citations.
    Research mode requires use_llm=true; otherwise falls back to fast mode
    with a warning."""
    from universal_statistician.agent.modes import answer_question_with_mode

    planner = _resolve_planner(body.use_llm)
    llm_agent, answer_writer, verifier = _resolve_agent_components(body.use_llm)

    run = _call(
        answer_question_with_mode,
        _engine,
        body.question,
        mode=body.mode,
        planner=planner,
        llm_agent=llm_agent,
        answer_writer=answer_writer,
        verifier=verifier,
    )

    result = run.result.as_dict()
    result["mode_used"] = run.mode_used
    if run.investigation is not None and run.investigation.verification_results:
        result["verification"] = run.investigation.verification_results[-1]
    else:
        result["verification"] = None
    if body.debug and run.investigation is not None:
        state = run.investigation
        result["debug"] = {
            "iteration_count": state.iteration_count,
            "tool_call_history": [record.as_dict() for record in state.tool_call_history],
            "candidates_considered": [c.as_dict() for c in state.candidates_considered],
            "candidates_rejected": [c.as_dict() for c in state.candidates_rejected],
            "verification_results": list(state.verification_results),
            "investigator_summary": state.investigator_summary,
        }
    return result

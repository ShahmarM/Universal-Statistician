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
    """Phase K: allowed origins configurable via USTAT_CORS_ORIGINS (a
    comma-separated list, e.g. "https://app.example.com,https://example.com")
    for a real deployment. Unset keeps this project's original personal/
    local-tool default: any localhost/127.0.0.1 origin, since the dashboard
    may be served by Vite's dev server or as static files on whatever local
    port either picks — never widened to "allow everything" implicitly."""
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
    """Basic in-process rate limiting (Phase K) — a fixed-window counter per
    client IP. Deliberately not Redis-backed: this project's stated scope is
    a personal/local tool run as a single process (see plan.md), and a
    single dict behind a lock is the whole job for that case. This would
    need a shared backend to work correctly across multiple worker
    processes/replicas — documented here, not silently assumed away, so a
    future multi-process deployment doesn't get a false sense of protection.
    """

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


#: USTAT_RATE_LIMIT_REQUESTS=0 disables rate limiting entirely (the default
#: --- a personal/local tool with no untrusted traffic doesn't need it on by
#: default; a real deployment sets both env vars).
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
    """Run a tools.py call and translate errors into HTTP responses instead
    of a raw 500 — same principle as cli.py's _run.

    The broad `except Exception` at the end matters more than it looks: a
    live browser test against this endpoint (get_series hitting a
    network-blocked SDMX host) showed that letting an unhandled provider
    exception escape doesn't just produce a 500 — the browser reports it as
    a CORS failure instead, because the response never completes normally
    enough for CORSMiddleware to attach its headers. Catching it here and
    raising a normal HTTPException fixes both the misleading error and gives
    the client an actual explanation.
    """
    try:
        return fn(*args, **kwargs)
    except UnknownSourceError as exc:
        # UnknownSourceError subclasses KeyError, whose __str__ double-quotes
        # the message — unwrap args[0] as cli.py's _run() does.
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
    """Liveness/readiness check (Phase K). Deliberately touches only the
    local catalog (a SQLite read, see Catalog.summary()) — never an
    upstream provider API. A deploy/orchestration probe must be able to
    tell the process is up even when every external statistics API is
    unreachable; that's the whole point of this project's "startup never
    requires upstream APIs" rule (see core/engine.py's _open_catalog())."""
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
    #: Use Anthropic-backed LLM components (server's ANTHROPIC_API_KEY env
    #: var) instead of the deterministic defaults — AnthropicPlanner for
    #: fast mode's interpretation step, and (agent Phase 8) AnthropicAgent/
    #: AnthropicAnswerWriter/AnthropicVerifier for research mode's
    #: investigation/answer-writing/verification steps. Never accepts a key
    #: in the request body — only a boolean opt-in into whatever the server
    #: process already has configured, the same source `ustat chat`/
    #: `ustat plan --llm` use.
    use_llm: bool = False
    #: "auto" (default) | "fast" | "research" — an explicit override always
    #: wins over auto's question-text heuristic (agent/modes.py::select_mode,
    #: task section 7). Only meaningful for /ask; /plan always uses the
    #: legacy fast-mode planner regardless of this field.
    mode: str = "auto"
    #: Include the investigation audit trail (tool calls, candidates
    #: considered/rejected, iteration count, the investigator's own debug
    #: summary) in the response under "debug". Never includes anything
    #: resembling hidden chain-of-thought — there is none to expose, since
    #: the investigator only ever produces tool calls and a short final
    #: summary, not extended-thinking tokens. Ignored outside research mode
    #: (nothing to show).
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
    """The three agent-mode LLM roles (Phase 8), all sharing one client —
    same ANTHROPIC_API_KEY gate as _resolve_planner, and the same "None
    means run without an LLM" contract agent/modes.py already honors by
    falling back to fast mode with a warning rather than erroring."""
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
    """Structured query plan for a question — interpretation and
    catalog-resolved candidate/selected indicators only, no retrieval.
    Debug/inspection endpoint (section 10)."""
    planner = _resolve_planner(body.use_llm)
    return _call(tools.build_plan, _engine, body.question, planner)


@app.post("/ask")
def ask(body: QuestionRequest) -> dict:
    """Full pipeline (task section 15): question -> mode selection (auto/
    fast/research, explicit override honored) -> investigation/plan ->
    retrieval -> transformations -> validation -> optional LLM verification
    -> answer + table + chart + citations.

    Fast mode is core/ask.py's original single-pass pipeline, unchanged.
    Research mode is agent/loop.py's iterative StatisticalAgent (agent
    Phases 1-7); it requires use_llm=true (an LLM has to drive the
    investigation) and otherwise falls back to fast mode with a warning
    rather than erroring, same principle RuleBasedPlanner already applies
    when no LLM is configured for planning.
    """
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

"""Interface-agnostic tool functions shared by the MCP server and the CLI.

Each function takes an explicit QueryEngine and returns plain, JSON-friendly
data (dicts/lists), never a dataclass — so the MCP server and the CLI (and
any future interface) serialize results the same way without re-implementing
this dispatch logic once per interface.
"""

from __future__ import annotations

from universal_statistician.core.compose import (
    compare_across_countries,
    compare_across_indicators,
    with_growth,
    with_rank,
    with_ratio,
)
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.query_plan import build_query_plan
from universal_statistician.planning.base import LLMPlanner
from universal_statistician.planning.rule_based_planner import RuleBasedPlanner


def search_indicator(engine: QueryEngine, query: str, limit: int = 20) -> list[dict]:
    return [m.as_dict() for m in engine.search_indicator(query, limit=limit)]


def get_series(
    engine: QueryEngine,
    source_id: str,
    indicator_id: str,
    ref_area: str,
    start_period: str | None = None,
    end_period: str | None = None,
) -> dict:
    result = engine.get_series(
        source_id, indicator_id, ref_area, start_period=start_period, end_period=end_period
    )
    return result.as_dict()


def list_sources(engine: QueryEngine) -> list[dict]:
    return engine.list_sources()


def refresh_catalog(engine: QueryEngine, source_id: str | None = None) -> list[dict]:
    return [
        {
            "source_id": r.source_id,
            "added": r.added,
            "updated": r.updated,
            "unchanged": r.unchanged,
            "errors": list(r.errors),
        }
        for r in engine.refresh_catalog(source_id)
    ]


def catalog_stats(engine: QueryEngine) -> dict[str, int]:
    return engine.catalog_stats()


def build_plan(engine: QueryEngine, question: str, planner: LLMPlanner | None = None) -> dict:
    """Interpret a natural-language question into an inspectable QueryPlan,
    without retrieving anything. Defaults to RuleBasedPlanner (no external
    dependency) so this stays usable without an LLM configured — callers
    that want real NL understanding pass an AnthropicPlanner explicitly
    (see cli.py's `plan --llm`)."""
    planner = planner or RuleBasedPlanner()
    interpretation = planner.interpret(question)
    return build_query_plan(question, interpretation, engine).as_dict()


def describe_source(engine: QueryEngine, source_id: str) -> dict:
    return engine.describe_source(source_id)


def compare(
    engine: QueryEngine,
    source_id: str,
    *,
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
    """Build a comparison table: either cross-country (one indicator across
    several areas — pass indicator_id + ref_areas) or cross-indicator (several
    indicators for one area — pass indicator_ids + ref_area). Exactly one of
    the two shapes must be given."""
    if indicator_id is not None and ref_areas:
        table = compare_across_countries(
            engine,
            source_id,
            indicator_id,
            ref_areas,
            start_period=start_period,
            end_period=end_period,
        )
    elif indicator_ids and ref_area is not None:
        table = compare_across_indicators(
            engine,
            source_id,
            indicator_ids,
            ref_area,
            start_period=start_period,
            end_period=end_period,
        )
    else:
        raise ValueError(
            "compare() needs either (indicator_id, ref_areas) for a cross-country "
            "comparison, or (indicator_ids, ref_area) for a cross-indicator one."
        )

    if growth:
        table = with_growth(table)
    if ratio_to:
        table = with_ratio(table, baseline_key=ratio_to)
    if rank:
        table = with_rank(table)

    return table.as_dict()

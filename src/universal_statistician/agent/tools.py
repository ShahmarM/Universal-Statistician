"""Trusted statistical tool layer.

Every function is deterministic and only wraps the existing core modules
(Catalog, QueryEngine, compose, validation, provenance). The LLM may only
supply search text, hints, and catalog_id/result_id values copied from
previous tool results — never numeric values, formulas, or validation
verdicts. Inputs/outputs are plain JSON dicts (what a tool-use API
exchanges); agent/state.py's dataclasses are the source of truth.
"""

from __future__ import annotations

import time
from statistics import median

from universal_statistician.agent.expressions import CALCULATE_OPERATIONS, CalculationRequest, execute_calculation
from universal_statistician.agent.state import (
    CandidateSummary,
    InvestigationState,
    RejectedCandidate,
    RetrievedResult,
    catalog_id as make_catalog_id,
    parse_catalog_id,
)
from universal_statistician.core.compose import ComparisonColumn, ComparisonTable
from universal_statistician.core.geography import provider_ref_area, resolve_geography
from universal_statistician.core.provenance import resolve_provenance
from universal_statistician.core.validation import validate_table

# ---- search_series ----------------------------------------------------


#: Lexical rank stays the dominant signal; metadata signals only nudge.
#: Weights mirror core/selection.py::score_candidate's scale.
_LEXICAL_RANK_WEIGHT = 1.0
_LEXICAL_RANK_SPAN = 10


def _rerank_score(
    meta,
    lexical_rank: int,
    *,
    geography: str | None,
    frequency: str | None,
    unit_hint: str | None,
    period_covered: bool | None,
) -> tuple[float, list[str]]:
    """Re-rank one search candidate. Metadata signals nudge, never hard-
    filter; unknown metadata leaves the score unchanged."""
    score = max(0.0, _LEXICAL_RANK_SPAN - lexical_rank) * _LEXICAL_RANK_WEIGHT
    reasons = [f"catalog lexical rank {lexical_rank}"]

    if geography:
        if meta.geographic_coverage is None:
            reasons.append("geographic_coverage unknown (not penalized)")
        elif geography.upper() in {g.upper() for g in meta.geographic_coverage}:
            score += 1.5
            reasons.append(f"covers requested geography {geography!r}")
        else:
            score -= 1.0
            reasons.append(f"geographic_coverage does not list requested geography {geography!r}")

    if frequency:
        if meta.frequency is None:
            reasons.append("frequency unknown (not penalized)")
        elif meta.frequency.strip().upper() == frequency.strip().upper():
            score += 1.0
            reasons.append(f"matches requested frequency {frequency!r}")
        else:
            score -= 0.5
            reasons.append(f"frequency {meta.frequency!r} differs from requested {frequency!r}")

    if unit_hint:
        if meta.unit is None:
            reasons.append("unit unknown (not penalized)")
        elif unit_hint.strip().lower() in meta.unit.lower():
            score += 0.5
            reasons.append(f"unit matches hint {unit_hint!r}")
        else:
            # Not penalized: units are phrased inconsistently across sources,
            # so a substring miss is weak evidence.
            reasons.append(f"unit {meta.unit!r} does not obviously match hint {unit_hint!r} (not penalized)")

    if period_covered is True:
        # Confirmed by real retrieval in this investigation, so it may
        # outweigh a marginally-better lexical match.
        score += 1.5
        reasons.append("already confirmed in this investigation to cover the requested period")
    elif period_covered is False:
        score -= 1.5
        reasons.append("already confirmed in this investigation to NOT cover the requested period")

    return score, reasons


def search_series(
    state: InvestigationState,
    *,
    query: str,
    geography: str | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
    frequency: str | None = None,
    unit_hint: str | None = None,
    source_preference: str | None = None,
    limit: int = 10,
) -> dict:
    """Find candidate series in the local catalog. Lexical relevance is the
    base ranking; geography/frequency/unit_hint/period nudge the order via
    _rerank_score(). source_preference is the one hard filter. The order is
    a hint, never a selection."""
    limit = max(1, min(limit, 25))
    # Fetch a wider pool when re-ranking, so a better metadata match just
    # past the lexical cutoff can still surface.
    reranking = any([geography, frequency, unit_hint, start_period, end_period])
    fetch_limit = min(limit * 3, 50) if (source_preference or reranking) else limit
    results = state.engine.search_indicator(query, limit=fetch_limit)
    if source_preference:
        results = [r for r in results if r.source_id == source_preference]

    period_geo_filter = [geography] if geography else []
    scored = []
    for lexical_rank, meta in enumerate(results):
        period_covered: bool | None = None
        if start_period or end_period:
            cid = make_catalog_id(meta.source_id, meta.indicator_id)
            earliest, latest, covered_for = _known_period_coverage(state, cid, period_geo_filter)
            if earliest is not None:
                period_covered = not (
                    (start_period and start_period > latest) or (end_period and end_period < earliest)
                )
        score, reasons = _rerank_score(
            meta, lexical_rank,
            geography=geography, frequency=frequency, unit_hint=unit_hint, period_covered=period_covered,
        )
        scored.append((score, lexical_rank, meta, reasons))

    # Tiebreak on lexical_rank so equal scores keep the catalog's order.
    scored.sort(key=lambda item: (-item[0], item[1]))

    already_considered = {c.catalog_id for c in state.candidates_considered}
    candidates: list[CandidateSummary] = []
    for final_rank, (_score, lexical_rank, meta, reasons) in enumerate(scored[:limit]):
        cid = make_catalog_id(meta.source_id, meta.indicator_id)
        summary = CandidateSummary(
            catalog_id=cid,
            source_id=meta.source_id,
            indicator_id=meta.indicator_id,
            title=meta.name,
            organization=meta.source_organization,
            dataset_id=meta.dataset_id,
            unit=meta.unit,
            frequency=meta.frequency,
            price_basis=meta.semantics.price_basis if meta.semantics else None,
            seasonally_adjusted=meta.semantics.seasonally_adjusted if meta.semantics else None,
            geographic_coverage=meta.geographic_coverage,
            official_url=meta.official_url,
            search_rank=final_rank,
            search_score_note="; ".join(reasons),
        )
        candidates.append(summary)
        if cid not in already_considered:
            already_considered.add(cid)
            state.candidates_considered.append(summary)

    return {
        "query": query,
        "candidates": [c.as_dict() for c in candidates],
        "count": len(candidates),
        "note": (
            "Ranked by catalog lexical relevance, nudged by how well each "
            "candidate's known metadata matches geography/frequency/unit_hint/"
            "period (never a hard filter except source_preference) — still "
            "not semantic correctness for this question. A bare concept query "
            "commonly returns several plausible candidates (e.g. nominal vs. "
            "real GDP, national vs. modeled-estimate unemployment) — use "
            "inspect_series to compare them on unit/frequency/price_basis "
            "before choosing, and reject_candidate to record why one was ruled "
            "out."
        ),
    }


# ---- inspect_series -----------------------------------------------------


def inspect_series(state: InvestigationState, *, catalog_id: str) -> dict:
    """Full normalized metadata for one catalog_id, plus real observed time
    coverage if it was already retrieved in this investigation. Absent
    metadata comes back as None, never guessed."""
    source_id, indicator_id = parse_catalog_id(catalog_id)
    meta = state.engine.describe_indicator(source_id, indicator_id)
    if meta is None:
        return {
            "catalog_id": catalog_id,
            "found": False,
            "error": "No catalog entry found for this catalog_id.",
        }
    earliest, latest, period_known_for = _known_period_coverage(state, catalog_id, [])
    return {
        "catalog_id": catalog_id,
        "found": True,
        "source_id": meta.source_id,
        "indicator_id": meta.indicator_id,
        "title": meta.name,
        "description": meta.description,
        "dataset_id": meta.dataset_id,
        "unit": meta.unit,
        "frequency": meta.frequency,
        "geographic_coverage": (
            list(meta.geographic_coverage) if meta.geographic_coverage is not None else None
        ),
        "dimensions": [d.as_dict() for d in meta.dimensions] if meta.dimensions is not None else None,
        "source_organization": meta.source_organization,
        "official_url": meta.official_url,
        "last_updated": meta.last_updated,
        "keywords": list(meta.keywords) if meta.keywords is not None else None,
        "semantics": meta.semantics.as_dict() if meta.semantics is not None else None,
        "earliest_period": earliest,
        "latest_period": latest,
        "period_coverage_known": earliest is not None,
        "period_coverage_known_for_geographies": period_known_for,
        "note": (
            "Time coverage above reflects real observations already retrieved "
            "earlier in this investigation, if any -- unknown (null), not "
            "absent, until retrieve_series has actually run on this catalog_id. "
            "Per-observation actual/provisional/forecast status is not "
            "summarized here (it can vary period to period); "
            "inspect_provenance reports it for one specific observation once "
            "retrieved, when the source's own data supplies it."
        ),
    }


# ---- check_coverage -----------------------------------------------------


def _known_period_coverage(
    state: InvestigationState, cid: str, geographies: list[str]
) -> tuple[str | None, str | None, list[str]]:
    """Earliest/latest period observed for `cid`, from observations already
    retrieved in this investigation only — never estimated. (None, None, [])
    means "not retrieved yet" (unknown), not "no data"."""
    geo_set = {g.upper() for g in geographies} if geographies else None
    periods: set[str] = set()
    covered: list[str] = []
    for record in state.retrieved.values():
        if record.catalog_id != cid:
            continue
        if geo_set is not None and record.ref_area.upper() not in geo_set:
            continue
        obs_periods = [o.period for o in record.series.observations if o.value is not None]
        if obs_periods:
            periods.update(obs_periods)
            covered.append(record.ref_area)
    if not periods:
        return None, None, []
    ordered = sorted(periods)
    return ordered[0], ordered[-1], covered


def check_coverage(
    state: InvestigationState,
    *,
    catalog_ids: list[str],
    geographies: list[str],
    start_period: str | None = None,
    end_period: str | None = None,
    frequency: str | None = None,
) -> dict:
    """Cheap pre-check before spending a retrieval call. Geography and
    frequency come from catalog metadata; period coverage only from
    observations already retrieved in this investigation — never a guess."""
    canonical_geographies = [resolve_geography(g) for g in geographies]
    results = []
    for cid in catalog_ids:
        source_id, indicator_id = parse_catalog_id(cid)
        meta = state.engine.describe_indicator(source_id, indicator_id)
        if meta is None:
            results.append({"catalog_id": cid, "error": "No catalog entry found for this catalog_id."})
            continue

        available: list[str] | None = None
        missing: list[str] | None = None
        if meta.geographic_coverage is not None:
            covered = {g.upper() for g in meta.geographic_coverage}
            available = [g for g in canonical_geographies if g.upper() in covered]
            missing = [g for g in canonical_geographies if g.upper() not in covered]

        warnings: list[str] = []
        frequency_compatible = None
        if frequency and meta.frequency:
            frequency_compatible = frequency.strip().upper() == meta.frequency.strip().upper()
            if not frequency_compatible:
                warnings.append(
                    f"Requested frequency {frequency!r} differs from this indicator's "
                    f"{meta.frequency!r}."
                )
        if meta.geographic_coverage is None:
            warnings.append(
                "Catalog does not record geographic_coverage for this indicator — "
                "country availability is unknown until retrieved."
            )
        else:
            if missing:
                warnings.append(f"Requested area(s) not in catalog geographic_coverage: {missing}")

        earliest, latest, period_known_for = _known_period_coverage(state, cid, canonical_geographies)
        period_coverage_known = earliest is not None
        if period_coverage_known:
            if start_period and start_period > latest:
                warnings.append(
                    f"Already-retrieved data for {period_known_for} only goes up to "
                    f"{latest!r}, before the requested start_period {start_period!r}."
                )
            if end_period and end_period < earliest:
                warnings.append(
                    f"Already-retrieved data for {period_known_for} only starts at "
                    f"{earliest!r}, after the requested end_period {end_period!r}."
                )

        results.append(
            {
                "catalog_id": cid,
                "requested_geographies": canonical_geographies,
                "available_geographies": available,
                "missing_geographies": missing,
                "geographic_coverage_known": meta.geographic_coverage is not None,
                "requested_start_period": start_period,
                "requested_end_period": end_period,
                "earliest_period": earliest,
                "latest_period": latest,
                "period_coverage_known": period_coverage_known,
                "period_coverage_known_for_geographies": period_known_for,
                "frequency_compatible": frequency_compatible,
                "warnings": warnings,
            }
        )

    return {
        "results": results,
        "note": (
            "Period coverage comes only from real observations already "
            "retrieved earlier in this investigation (retrieve_series must run "
            "on a catalog_id before its period coverage is known here) — never "
            "a guess or an estimate. geographic_coverage, when present, IS real "
            "catalog metadata, not a guess; when absent, availability is "
            "genuinely unknown, not assumed false."
        ),
    }


# ---- retrieve_series ------------------------------------------------------


def retrieve_series(
    state: InvestigationState,
    *,
    catalog_id: str,
    geographies: list[str],
    start_period: str | None = None,
    end_period: str | None = None,
) -> dict:
    """Retrieve official observations for one catalog_id across one or more
    geographies. Every value returned here comes directly from a provider
    response or cache — this is the only tool that produces numbers."""
    source_id, indicator_id = parse_catalog_id(catalog_id)
    meta = state.engine.describe_indicator(source_id, indicator_id)
    outcomes = []

    for geo in geographies:
        canonical = resolve_geography(geo)
        # One count per actual provider/cache request, success or failure.
        state.provider_call_count += 1
        try:
            series = state.engine.get_series(
                source_id,
                indicator_id,
                provider_ref_area(canonical, source_id=source_id),
                start_period=start_period,
                end_period=end_period,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the LLM, not swallowed
            message = f"Failed to retrieve {catalog_id} for {geo!r}: {exc}"
            state.warnings.append(message)
            outcomes.append({"geography": canonical, "ok": False, "error": str(exc)})
            continue

        result_id = state.new_result_id()
        column = ComparisonColumn(
            key=result_id,
            label=f"{meta.name if meta else indicator_id} ({canonical})",
            attribution=series.attribution,
            unit=series.unit or (meta.unit if meta else None),
            frequency=series.frequency,
            indicator_id=indicator_id,
            ref_area=canonical,
            semantics=series.semantics or (meta.semantics if meta else None),
        )
        values = {obs.period: obs.value for obs in series.observations}
        state.add_column(column, values)
        state.retrieved[result_id] = RetrievedResult(
            result_id=result_id, catalog_id=catalog_id, ref_area=canonical, series=series
        )

        periods_with_values = sorted(p for p, v in values.items() if v is not None)
        latest = periods_with_values[-1] if periods_with_values else None
        outcomes.append(
            {
                "geography": canonical,
                "ok": True,
                "result_id": result_id,
                "observation_count": len(series.observations),
                "earliest_period": periods_with_values[0] if periods_with_values else None,
                "latest_period": latest,
                "latest_value": values.get(latest) if latest else None,
                "unit": column.unit,
                "frequency": column.frequency,
                "attribution": series.attribution.as_dict(),
            }
        )

    return {"catalog_id": catalog_id, "results": outcomes}


# ---- compare_series -------------------------------------------------------


def compare_series(state: InvestigationState, *, result_ids: list[str]) -> dict:
    """Deterministic comparison of two or more already-retrieved results —
    never a verdict on which is "correct", only evidence for the LLM to
    reason about."""
    if len(result_ids) < 2:
        return {"error": "compare_series needs at least two result_ids."}

    records: list[RetrievedResult] = []
    columns: list[ComparisonColumn] = []
    for rid in result_ids:
        record = state.retrieved.get(rid)
        if record is None:
            return {
                "error": (
                    f"Unknown result_id {rid!r} — must be a result_id returned by "
                    "retrieve_series, not a catalog_id or an invented value."
                )
            }
        records.append(record)
        columns.append(state.table.column(rid))

    # Read from the enriched ComparisonColumn (provider value with catalog
    # fallback), not the raw SeriesResult, which often lacks unit/semantics.
    units = sorted({c.unit for c in columns if c.unit})
    frequencies = sorted({c.frequency for c in columns if c.frequency})
    price_bases = sorted(
        {c.semantics.price_basis for c in columns if c.semantics and c.semantics.price_basis}
    )
    seasonal_flags = sorted(
        {
            str(c.semantics.seasonally_adjusted)
            for c in columns
            if c.semantics and c.semantics.seasonally_adjusted is not None
        }
    )
    geographies = sorted({r.ref_area for r in records})

    period_sets = [{o.period for o in r.series.observations if o.value is not None} for r in records]
    overlapping_periods = sorted(set.intersection(*period_sets)) if period_sets else []

    numeric_comparison = None
    if len(records) == 2 and overlapping_periods:
        values_a = {o.period: o.value for o in records[0].series.observations}
        values_b = {o.period: o.value for o in records[1].series.observations}
        diffs = [
            (p, values_a[p] - values_b[p])
            for p in overlapping_periods
            if values_a.get(p) is not None and values_b.get(p) is not None
        ]
        if diffs:
            sorted_by_magnitude = sorted(diffs, key=lambda item: abs(item[1]))
            # median(|A - B|), not |median(A - B)| — these diverge when the
            # signed diffs aren't symmetric around zero.
            median_abs = median(abs(d) for _p, d in diffs)
            numeric_comparison = {
                "overlapping_period_count": len(diffs),
                "mean_absolute_difference": sum(abs(d) for _p, d in diffs) / len(diffs),
                "median_absolute_difference": median_abs,
                "largest_differences": [
                    {"period": p, "difference": d} for p, d in reversed(sorted_by_magnitude[-3:])
                ],
            }

    return {
        "result_ids": result_ids,
        "attributions": [r.series.attribution.as_dict() for r in records],
        "units": units,
        "units_compatible": len(units) <= 1,
        "frequencies": frequencies,
        "frequency_compatible": len(frequencies) <= 1,
        "price_bases": price_bases,
        "seasonally_adjusted_values": seasonal_flags,
        "geographies": geographies,
        "overlapping_periods": overlapping_periods,
        "numeric_comparison": numeric_comparison,
        "disclaimer": (
            "Numerical disagreement between series does not by itself indicate an "
            "error in either one — differing methodology, revision status, "
            "reference period, or definition can all produce legitimate "
            "differences. Only the metadata facts above (unit/frequency/"
            "price_basis/seasonal adjustment/attribution) are established; any "
            "methodological explanation beyond what this metadata states is "
            "unconfirmed and must be reported as such, not asserted."
        ),
    }


# ---- calculate --------------------------------------------------------


def calculate(state: InvestigationState, **kwargs) -> dict:
    """Execute one deterministic transformation over already-retrieved/
    derived result_ids: validates kwargs into a CalculationRequest (closed
    operation enum, never arbitrary code) and dispatches onto compose.py's
    with_*() functions."""
    try:
        request = CalculationRequest.from_dict(kwargs)
    except ValueError as exc:
        operation = str(kwargs.get("operation") or "").strip().lower()
        return {"operation": operation, "error": str(exc)}
    return execute_calculation(state, request)


# ---- validate -----------------------------------------------------------


def validate(
    state: InvestigationState,
    *,
    result_ids: list[str] | None = None,
    requested_geographies: list[str] | None = None,
    requested_start_period: str | None = None,
    requested_end_period: str | None = None,
) -> dict:
    """Run the existing deterministic validation engine (core/validation.py)
    over the given results, or the whole investigation table if none are
    named."""
    table = state.table
    if result_ids:
        unknown = [r for r in result_ids if r not in {c.key for c in table.columns}]
        if unknown:
            return {"error": f"Unknown result_id(s): {unknown}"}
        keys = set(result_ids)
        table = ComparisonTable(
            columns=tuple(c for c in table.columns if c.key in keys),
            values={(p, k): v for (p, k), v in table.values.items() if k in keys},
            cell_dependencies={
                key: deps for key, deps in table.cell_dependencies.items() if key[1] in keys
            },
        )

    result = validate_table(
        table,
        requested_geographies=tuple(requested_geographies or ()),
        requested_start_period=requested_start_period,
        requested_end_period=requested_end_period,
    )
    payload = result.as_dict()
    state.validation_results.append(payload)
    return payload


# ---- inspect_provenance ---------------------------------------------------


def _attach_observation_status(state: InvestigationState, node: dict) -> None:
    """Enrich a provenance dict (in place, recursively) with each
    observation leaf's actual/provisional/forecast status, read back from
    state.retrieved. None ("unknown") when the provider didn't supply one."""
    if node.get("kind") == "observation":
        record = state.retrieved.get(node.get("column_key"))
        status = None
        if record is not None:
            for obs in record.series.observations:
                if obs.period == node.get("period"):
                    status = obs.status
                    break
        node["status"] = status
    for child in node.get("inputs") or ():
        _attach_observation_status(state, child)


def inspect_provenance(state: InvestigationState, *, result_id: str, period: str | None = None) -> dict:
    """Resolve one result (base or derived) back to the exact official
    observation(s) it came from, including each observation's status when
    the source supplied one."""
    if result_id not in {c.key for c in state.table.columns}:
        return {"error": f"Unknown result_id {result_id!r}"}
    if period is None:
        periods_with_values = [
            p for p in state.table.periods() if state.table.value_at(p, result_id) is not None
        ]
        if not periods_with_values:
            return {"error": f"No observations found for {result_id!r}"}
        period = periods_with_values[-1]

    try:
        provenance = resolve_provenance(state.table, result_id, period)
    except ValueError as exc:
        return {"error": str(exc)}

    payload = provenance.as_dict()
    _attach_observation_status(state, payload)
    state.provenance_references.append(payload)
    return payload


# ---- reject_candidate ------------------------------------------------------


def reject_candidate(state: InvestigationState, *, catalog_id: str, reason: str) -> dict:
    """Record that a candidate was inspected and ruled out, making the
    rejection auditable."""
    state.candidates_rejected.append(RejectedCandidate(catalog_id=catalog_id, reason=reason))
    return {"catalog_id": catalog_id, "recorded": True}


# ---- tool schemas (Anthropic tool-use format) -----------------------------
# Kept next to the functions they describe so they can't drift.

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "search_series",
        "description": (
            "Search the local catalog of official statistical series by concept. "
            "Returns ranked candidates with metadata (unit, frequency, price basis, "
            "geographic coverage, source) to reason about — the top-ranked result is "
            "a lexical/metadata match, not a semantic verdict; inspect before choosing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Concept/search text, e.g. 'real GDP growth'."},
                "geography": {"type": ["string", "null"], "description": "A country/area, if known — boosts candidates whose catalog geographic_coverage includes it; never excludes a candidate outright (coverage is often unrecorded, not absent)."},
                "start_period": {"type": ["string", "null"], "description": "Boosts candidates already confirmed (via a prior retrieve_series in this investigation) to cover this period; has no effect on a candidate never retrieved yet."},
                "end_period": {"type": ["string", "null"], "description": "Same effect as start_period."},
                "frequency": {"type": ["string", "null"], "description": "e.g. 'A', 'Q', 'M' — boosts a matching candidate, nudges down a mismatched one."},
                "unit_hint": {"type": ["string", "null"], "description": "Boosts a candidate whose catalog unit text contains this — never penalized on a miss, since units are phrased inconsistently across sources."},
                "source_preference": {"type": ["string", "null"], "description": "A specific source_id to restrict results to, e.g. 'WB_WDI' — the one hard filter here, unlike the other parameters."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect_series",
        "description": (
            "Full normalized metadata for one specific candidate, identified by the catalog_id "
            "a prior search_series/inspect_series result returned: definition, source/dataset, "
            "unit, frequency, statistical semantics (nominal/real, base_year, seasonal "
            "adjustment, ...), geographic coverage, and -- if this catalog_id was already "
            "retrieved earlier in this investigation -- real observed time coverage. Any field "
            "the catalog doesn't have comes back null, never guessed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "catalog_id": {"type": "string", "description": "Exactly as returned by search_series, e.g. 'WB_WDI::NY_GDP_MKTP_KD'."},
            },
            "required": ["catalog_id"],
        },
    },
    {
        "name": "check_coverage",
        "description": (
            "Inexpensive check of whether candidate(s) can plausibly answer the question "
            "before retrieving full data. Geography/frequency checks are catalog metadata; "
            "period coverage (earliest/latest_period) is only known once a catalog_id has "
            "already been retrieved earlier in this investigation -- unknown, not absent, "
            "before that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "catalog_ids": {"type": "array", "items": {"type": "string"}},
                "geographies": {"type": "array", "items": {"type": "string"}},
                "start_period": {"type": ["string", "null"]},
                "end_period": {"type": ["string", "null"]},
                "frequency": {"type": ["string", "null"]},
            },
            "required": ["catalog_ids", "geographies"],
        },
    },
    {
        "name": "retrieve_series",
        "description": "Retrieve official observations for one catalog_id across one or more geographies. The only tool that produces numeric values.",
        "input_schema": {
            "type": "object",
            "properties": {
                "catalog_id": {"type": "string"},
                "geographies": {"type": "array", "items": {"type": "string"}},
                "start_period": {"type": ["string", "null"]},
                "end_period": {"type": ["string", "null"]},
            },
            "required": ["catalog_id", "geographies"],
        },
    },
    {
        "name": "compare_series",
        "description": "Deterministically compare two or more already-retrieved results (unit/frequency/semantics/overlap/numeric differences). Returns evidence only, never a verdict on which is 'correct'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "result_id values from prior retrieve_series calls, never catalog_ids or invented values.",
                },
            },
            "required": ["result_ids"],
        },
    },
    {
        "name": "calculate",
        "description": (
            "Execute one deterministic statistical transformation, referencing only "
            "result_ids already produced by retrieve_series/calculate — never a raw "
            "number. Wraps this project's existing calculation engine; the operation "
            "must be one of the supported values, nothing else is executed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(CALCULATE_OPERATIONS)},
                "input": {"type": ["string", "null"], "description": "Single-input operations (growth, cagr, cumulative_growth, index, moving_average): one result_id."},
                "inputs": {"type": "array", "items": {"type": "string"}, "description": "Multi-input operations (sum, average, rank): two or more result_ids."},
                "numerator": {"type": ["string", "null"], "description": "share/per_capita only."},
                "denominator": {"type": ["string", "null"], "description": "share/per_capita only."},
                "left": {"type": ["string", "null"], "description": "difference only."},
                "right": {"type": ["string", "null"], "description": "difference only."},
                "weights": {"type": ["object", "null"], "description": "weighted_average only: {result_id: weight}, at least two entries."},
                "base_period": {"type": ["string", "null"], "description": "index only."},
                "base_value": {"type": "number", "default": 100.0, "description": "index only."},
                "start_period": {"type": ["string", "null"], "description": "cagr/cumulative_growth only."},
                "end_period": {"type": ["string", "null"], "description": "cagr/cumulative_growth only."},
                "window": {"type": ["integer", "null"], "description": "moving_average only: window size >= 2."},
                "output_name": {"type": ["string", "null"], "description": "Optional human-readable label for the result."},
            },
            "required": ["operation"],
        },
    },
    {
        "name": "validate",
        "description": "Run the deterministic validation engine over given result(s), or the whole investigation so far. A FAIL means a numerical answer must not be presented for the affected result(s).",
        "input_schema": {
            "type": "object",
            "properties": {
                "result_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "requested_geographies": {"type": ["array", "null"], "items": {"type": "string"}},
                "requested_start_period": {"type": ["string", "null"]},
                "requested_end_period": {"type": ["string", "null"]},
            },
            "required": [],
        },
    },
    {
        "name": "inspect_provenance",
        "description": (
            "Resolve one result (base or derived, at any depth) back to the exact official "
            "observation(s) it came from, including each observation's actual/provisional/"
            "forecast status when the source supplied one (null if not)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "result_id": {"type": "string"},
                "period": {"type": ["string", "null"], "description": "Defaults to the latest available period for this result."},
            },
            "required": ["result_id"],
        },
    },
    {
        "name": "reject_candidate",
        "description": "Record that a candidate was inspected and ruled out, with a short reason — makes the investigation auditable rather than only inferable from which candidates were never retrieved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "catalog_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["catalog_id", "reason"],
        },
    },
]


# ---- dispatch -------------------------------------------------------------

TOOL_FUNCTIONS = {
    "search_series": search_series,
    "inspect_series": inspect_series,
    "check_coverage": check_coverage,
    "retrieve_series": retrieve_series,
    "compare_series": compare_series,
    "calculate": calculate,
    "validate": validate,
    "inspect_provenance": inspect_provenance,
    "reject_candidate": reject_candidate,
}


def dispatch_tool(state: InvestigationState, name: str, arguments: dict) -> dict:
    """Entry point for every LLM tool_use block. Bad tool names/arguments
    return a structured error dict instead of raising, so the LLM can react."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name!r}. Available tools: {sorted(TOOL_FUNCTIONS)}."}
    try:
        return fn(state, **arguments)
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name!r}: {exc}"}
    except ValueError as exc:
        return {"error": str(exc)}


def timed_dispatch_tool(state: InvestigationState, name: str, arguments: dict) -> tuple[dict, float]:
    """dispatch_tool() plus wall-clock duration in milliseconds."""
    started = time.monotonic()
    result = dispatch_tool(state, name, arguments)
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    return result, duration_ms

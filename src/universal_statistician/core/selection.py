"""Deterministic, explainable Top-1 indicator selection per concept.

Used only by core/ask.py's legacy single-pass path (fast mode) — research
mode's LLM chooses via search/inspect/reject tools instead. Pure: every
candidate already carries the catalog metadata needed to score it.
Selection never merges candidates for one concept; mixed sources across
concepts are recorded in `assumptions`, never left implicit.
"""

from __future__ import annotations

import re
from dataclasses import replace

from universal_statistician.core.query_plan import CandidateIndicator, QueryPlan

#: Markers of a different *subject* than the plain concept (a subgroup or
#: per-person rate) even when the name shares every concept word — e.g.
#: "Population, female (% of total population)" vs concept "total
#: population". Deliberately narrow: NOT "%"/"rate"/"growth", which are
#: the natural unit for concepts like inflation. Symmetric: only flagged
#: when the concept text doesn't itself ask for the marker.
_QUALIFIER_MARKERS = ("female", "male", "per capita")


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _qualifiers_in(text_lower: str) -> frozenset[str]:
    # Whole-word matching — "male" is a substring of "female", so a bare
    # substring check would misfire.
    words = _words(text_lower)
    return frozenset(m for m in _QUALIFIER_MARKERS if (m in text_lower if " " in m or m == "%" else m in words))


def score_candidate(candidate: CandidateIndicator, plan: QueryPlan) -> tuple[float, tuple[str, ...]]:
    """Deterministic score plus the reasons behind it — the reasons are
    shown to users, the score itself never is."""
    score = 0.0
    reasons: list[str] = []

    # Carry the catalog's own search ranking through; without it, metadata
    # ties fell to an arbitrary alphabetical sort that picked noise entries
    # over flagship indicators.
    score += max(0, 5 - candidate.search_rank) * 0.3
    reasons.append(f"catalog search rank {candidate.search_rank}")

    concept_lower = candidate.concept.strip().lower()
    name_lower = candidate.name.lower()
    concept_words = _words(concept_lower)
    if concept_words and concept_words <= _words(name_lower):
        score += 2.0
        reasons.append(f"name contains every word of the requested concept {candidate.concept!r}")
    else:
        score += 0.5
        reasons.append("matched by catalog full-text search, not an exact name match")

    unexpected_qualifiers = _qualifiers_in(name_lower) - _qualifiers_in(concept_lower)
    if unexpected_qualifiers:
        score -= 2.5
        reasons.append(
            f"name mentions {sorted(unexpected_qualifiers)!r}, which the requested concept "
            f"{candidate.concept!r} does not ask for -- likely a different statistic"
        )

    if plan.geographies and candidate.geographic_coverage:
        requested = {g.upper() for g in plan.geographies}
        covered = {g.upper() for g in candidate.geographic_coverage}
        if requested <= covered:
            score += 1.5
            reasons.append("source's known geographic coverage includes every requested area")
        elif requested & covered:
            score += 0.25
            reasons.append("source's known geographic coverage includes some, not all, requested areas")
        else:
            score -= 1.0
            reasons.append("source's known geographic coverage does not include any requested area")

    if plan.frequency and candidate.frequency:
        if candidate.frequency == plan.frequency:
            score += 1.0
            reasons.append(f"frequency matches the requested {plan.frequency!r}")
        else:
            score -= 0.5
            reasons.append(
                f"frequency {candidate.frequency!r} differs from the requested {plan.frequency!r}"
            )

    if candidate.unit:
        score += 0.1
        reasons.append("unit is documented in the catalog")

    if candidate.semantics is not None and candidate.semantics.price_basis:
        score += 0.1
        reasons.append(
            f"statistical semantics documented in the catalog (price_basis="
            f"{candidate.semantics.price_basis!r})"
        )

    return score, tuple(reasons)


def select_indicators(plan: QueryPlan) -> QueryPlan:
    """Return a copy of `plan` with `selected_indicators` populated: the
    highest-scoring candidate per concept, ties broken deterministically by
    (source_id, indicator_id) so re-running selection on the same plan
    always picks the same one."""
    by_concept: dict[str, list[CandidateIndicator]] = {}
    for candidate in plan.candidate_indicators:
        by_concept.setdefault(candidate.concept, []).append(candidate)

    selected: list[CandidateIndicator] = []
    new_assumptions = list(plan.assumptions)
    sources_used: set[str] = set()

    for concept in plan.concepts:
        candidates = by_concept.get(concept)
        if not candidates:
            new_assumptions.append(f"No catalog candidates found for {concept!r}; nothing selected.")
            continue

        scored = sorted(
            ((c, *score_candidate(c, plan)) for c in candidates),
            key=lambda t: (-t[1], t[0].source_id, t[0].indicator_id),
        )
        best, best_score, best_reasons = scored[0]
        selected.append(best)
        sources_used.add(best.source_id)
        new_assumptions.append(
            f"Selected {best.source_id}/{best.indicator_id} for {concept!r}: " + "; ".join(best_reasons)
        )

    if len(sources_used) > 1 and len(selected) > 1:
        new_assumptions.append(
            "Selected indicators for different concepts come from different sources ("
            + ", ".join(sorted(sources_used))
            + ") — verify unit/frequency compatibility before combining them (see core/compose.py)."
        )

    return replace(plan, selected_indicators=tuple(selected), assumptions=tuple(new_assumptions))

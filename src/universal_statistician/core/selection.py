"""Source/indicator selection: pick one candidate per concept, deterministically
and explainably (section 12), from the catalog candidates a QueryPlan already
carries.

Agent-migration note (Phase 3, see docs/architecture/agent-migration-note.md):
this module's automatic, unreviewable Top-1 pick is now scoped to
core/ask.py's legacy single-pass path — fast mode, manual/API callers, and
existing tests that construct a QueryPlan directly. The primary
natural-language path (agent/loop.py's StatisticalAgent, "research" mode)
never calls select_indicators() at all: it chooses a series by calling
agent/tools.py's search_series (ranking is a *hint*, never a verdict),
inspect_series (to compare candidates' actual metadata), and
reject_candidate (recording *why* an unsuitable one was ruled out) before
ever calling retrieve_series — replacing "the ranking function chooses"
with "the LLM chooses, informed by ranking and real metadata, with its
reasoning recorded in InvestigationState for audit." This module is not
rewritten or removed; it remains exactly what fast mode and every existing
caller of build_query_plan()/select_indicators() needs.

Deliberately pure — no engine/network dependency: by the time a plan reaches
here (core/query_plan.py's build_query_plan()), every candidate already
carries the catalog metadata (unit, frequency, geographic_coverage) needed to
score it. Scoring criteria implemented: name-match quality, geographic
coverage of the requested areas, and frequency match against a requested
frequency — the criteria section 12 lists that this project can evaluate from
catalog metadata alone (freshness/completeness would need retrieved
observations, not just metadata, and belong with the validation layer,
Phase 9, not selection).

"Never silently mix incompatible series" (section 12) is enforced by
construction, not a warning bolted on afterward: selection always picks
exactly one source per concept (candidates for the same concept never get
merged), and if the concepts of a multi-concept plan end up resolved to
different sources, that fact is recorded in `assumptions` rather than left
implicit.
"""

from __future__ import annotations

import re
from dataclasses import replace

from universal_statistician.core.query_plan import CandidateIndicator, QueryPlan

#: Markers that indicate a candidate measures a different *subject* than
#: the plain concept requested -- one demographic subgroup, or a per-person
#: rate -- even when its name otherwise shares every word with the concept.
#: Live-discovered root cause this exists for: for concept "total
#: population", "Population, female (% of total population)" contains
#: every one of the concept's words (so the name-match bonus below fired)
#: while the actually-correct "Population, total" does not (word order
#: differs) -- unpenalized, this let a subgroup-share series outrank the
#: real headcount series as the fallback candidate, producing an answer
#: like "Azerbaijan's population was 50.98" (the female population
#: *share*, not a population count) with no indication anything was wrong.
#: Deliberately narrow: NOT "%"/"percent"/"growth"/"rate"/"share"/"ratio"
#: -- those are the *natural* unit for many concepts (inflation,
#: unemployment) without the concept text spelling it out, and penalizing
#: them flips the correct choice for exactly those cases (see
#: test_catalog_search_rank_breaks_ties_between_equally_named_candidates).
#: `qualifiers_in()` below is symmetric: it flags a marker only when the
#: concept text doesn't already ask for it, so "female population" as the
#: concept itself is unaffected.
_QUALIFIER_MARKERS = ("female", "male", "per capita")


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _qualifiers_in(text_lower: str) -> frozenset[str]:
    # Whole-word/whole-phrase matching, not bare substring -- "male" is a
    # substring of "female", so a naive `marker in text_lower` check would
    # (wrongly) claim "male" is mentioned by a name that only says "female".
    words = _words(text_lower)
    return frozenset(m for m in _QUALIFIER_MARKERS if (m in text_lower if " " in m or m == "%" else m in words))


def score_candidate(candidate: CandidateIndicator, plan: QueryPlan) -> tuple[float, tuple[str, ...]]:
    """Deterministic score plus the reasons behind it — the score itself is
    never shown to a user, the reasons are (see select_indicators())."""
    score = 0.0
    reasons: list[str] = []

    # Phase H: a real, live-discovered bug — without this, every candidate
    # for a concept only differs on catalog *metadata* (name substring,
    # geographic_coverage, frequency), which routinely ties (e.g. no
    # candidate has geographic_coverage data at all, or two names both
    # contain the concept word — one as the actual concept, one as an
    # unrelated compound modifier like "inflation-adjusted"). A tie then
    # fell through to an arbitrary (source_id, indicator_id) sort, which
    # picked US_CENSUS_ACS1's noise entry over WB_WDI's flagship indicator
    # for a plain "inflation" query purely because "US_CENSUS_ACS1" sorts
    # before "WB_WDI" alphabetically. Catalog.search() already ranks
    # candidates well (Phase C's composite scoring); this carries that
    # ranking through instead of discarding it and re-deriving a weaker
    # signal from scratch.
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

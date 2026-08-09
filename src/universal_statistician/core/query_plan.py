"""Structured query plan: the explicit, inspectable object between a
natural-language question and any retrieval/calculation.

This is the mechanism section 23's anti-hallucination rules are built on:
an LLM (or any planner) proposes a *QuestionInterpretation* — concepts,
geographies, periods, requested transformations — but never an indicator
code. `build_query_plan()` is the one place candidate indicators get filled
in, and it does so by calling `QueryEngine.search_indicator()` (the same
deterministic catalog search every interface already uses), never by
trusting a code the planner suggested. A QueryPlan is data, not behavior —
inspectable/debuggable before any retrieval happens (the "expose query
plans in developer/debug mode" requirement), and serializable for the
future `/ask` endpoint (Phase 11).

Source/indicator *selection* among candidates (Phase 8) and
validation *results* (Phase 9) are out of scope here — `selected_indicators`
and `validation_notes` exist on QueryPlan now, empty, because the shape is
specified by the target /ask response (this project's own task
description's section 10) and stabilizing it now avoids a breaking change
to every caller once those phases land, but nothing here computes them yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from universal_statistician.core.geography import resolve_geography
from universal_statistician.core.models import StatisticalSemantics

#: Words a real LLM planner has been observed writing in start_period/
#: end_period instead of leaving the field null (Phase G: caught live,
#: running AnthropicPlanner against real questions like "...to the latest
#: available year" — the model wrote the literal string "latest" as
#: end_period rather than omitting it). A period is meant to be an actual
#: value like "2015" or "2020-Q1"; every downstream consumer (get_series's
#: start/endPeriod params, compose.py's `end_period or periods[-1]`
#: "resolve to latest" idiom, validation's period-coverage check) already
#: treats None correctly as "not specified" — treating these words as None
#: here, structurally, is more robust than hoping every model always
#: follows the prompt's instruction to use null.
_NON_PERIOD_WORDS = {"latest", "present", "now", "current", "today", "n/a", "unknown", "none"}


def _clean_period(value: str | None) -> str | None:
    if value is None:
        return None
    if str(value).strip().lower() in _NON_PERIOD_WORDS:
        return None
    return value


@dataclass(frozen=True)
class CandidateIndicator:
    """One indicator the catalog matched for a requested concept — never
    proposed by an LLM, always the result of Catalog.search().

    Carries the same optional metadata IndicatorMeta does (unit, frequency,
    geographic_coverage, semantics) so source/indicator selection
    (core/selection.py, Phase 8) can score candidates without a second
    catalog lookup — this is exactly what engine.search_indicator() already
    returned in build_query_plan() below, just not previously kept.
    """

    indicator_id: str
    source_id: str
    name: str
    #: Which requested concept (QueryPlan.concepts) this candidate came from.
    concept: str
    unit: str | None = None
    frequency: str | None = None
    geographic_coverage: tuple[str, ...] | None = None
    #: Structured semantics (Phase F) — see StatisticalSemantics.
    semantics: StatisticalSemantics | None = None
    #: 0-based position in engine.search_indicator()'s results for this
    #: concept (Phase H: a real, live-discovered bug otherwise threw this
    #: information away). Catalog.search() already ranks the flagship/
    #: general indicator above narrow sub-breakdowns and cross-source noise
    #: (Phase C's composite scoring) — score_candidate() re-scores from
    #: catalog *metadata* alone and has no equivalent signal, so without
    #: this, a noise candidate that happens to tie on metadata (e.g. a
    #: substring name match plus no geographic_coverage data to
    #: differentiate on) could out-rank the true top search result on an
    #: arbitrary (source_id, indicator_id) tie-break. See
    #: core/selection.py::score_candidate().
    search_rank: int = 0

    def as_dict(self) -> dict:
        return {
            "indicator_id": self.indicator_id,
            "source_id": self.source_id,
            "name": self.name,
            "concept": self.concept,
            "unit": self.unit,
            "frequency": self.frequency,
            "geographic_coverage": (
                list(self.geographic_coverage) if self.geographic_coverage is not None else None
            ),
            "semantics": self.semantics.as_dict() if self.semantics is not None else None,
            "search_rank": self.search_rank,
        }


@dataclass(frozen=True)
class TransformationSpec:
    """One requested statistical operation — WHAT to compute, never a value
    (section: the planner "must describe WHAT statistical operation is
    required. It must NOT supply numerical data.").

    `operation` is the only required field. Operations that combine two or
    more series carry the *natural-language concepts* to look them up by —
    `numerator_concept`/`denominator_concept` (share, per_capita),
    `input_concept` (index), `left_concept`/`right_concept` (difference) —
    exactly like QuestionInterpretation.concepts: never an indicator code,
    always resolved through the same catalog search build_query_plan()
    already runs for the top-level concepts (see that function's handling
    of `referenced_concepts()` below — a transformation concept doesn't
    need to also be duplicated into the top-level `concepts` list, it gets
    searched either way).

    `inputs`/`weights` (weighted_average) are the one exception: given this
    project's table shape (one concept's values across several
    geographies, or several concepts' values for one geography — see
    core/compose.py's ComparisonTable), a weighted average combines across
    *geographies* of a single already-selected concept (e.g. "population-
    weighted average inflation across DEU/FRA/ITA"), so `inputs` holds
    geography/area codes, not concepts — those geographies must already be
    present in `QueryPlan.geographies` for their columns to exist to
    combine.

    Simple operations that don't reference another series at all (growth,
    yoy_growth, period_over_period_growth, absolute_change, pp_change,
    cagr, cumulative_growth, rank) only set `operation`; every other field
    stays None/empty, and core/ask.py dispatches them exactly as before
    this phase (a bare compose.py function call, no concept resolution
    needed since they operate on whatever's already in the table).
    """

    operation: str
    numerator_concept: str | None = None
    denominator_concept: str | None = None
    input_concept: str | None = None
    base_period: str | None = None
    base_value: float | None = None
    left_concept: str | None = None
    right_concept: str | None = None
    #: weighted_average only: geography/area codes to combine (see class
    #: docstring) — must line up 1:1 with `weights`.
    inputs: tuple[str, ...] = ()
    weights: tuple[float, ...] = ()
    #: Optional human-readable name for the derived column/answer this
    #: transformation produces (e.g. "non-oil share of GDP"); falls back to
    #: a generated key (see core/ask.py) when not given.
    output_name: str | None = None

    def concepts_referenced(self) -> tuple[str, ...]:
        """Every natural-language concept this transformation needs
        resolved through the catalog — never `inputs` (geography codes, not
        concepts; see class docstring)."""
        return tuple(
            c
            for c in (
                self.numerator_concept,
                self.denominator_concept,
                self.input_concept,
                self.left_concept,
                self.right_concept,
            )
            if c
        )

    def as_dict(self) -> dict:
        return {
            "operation": self.operation,
            "numerator_concept": self.numerator_concept,
            "denominator_concept": self.denominator_concept,
            "input_concept": self.input_concept,
            "base_period": self.base_period,
            "base_value": self.base_value,
            "left_concept": self.left_concept,
            "right_concept": self.right_concept,
            "inputs": list(self.inputs),
            "weights": list(self.weights),
            "output_name": self.output_name,
        }

    @staticmethod
    def from_dict(payload: "str | dict") -> "TransformationSpec":
        # Backward compatibility: a bare operation name (e.g. "growth"),
        # what every planner produced before structured transformations —
        # and still the natural way to write a simple, no-concept operation.
        if isinstance(payload, str):
            return TransformationSpec(operation=payload)
        return TransformationSpec(
            operation=payload["operation"],
            numerator_concept=payload.get("numerator_concept"),
            denominator_concept=payload.get("denominator_concept"),
            input_concept=payload.get("input_concept"),
            base_period=_clean_period(payload.get("base_period")),
            base_value=payload.get("base_value"),
            left_concept=payload.get("left_concept"),
            right_concept=payload.get("right_concept"),
            inputs=tuple(payload.get("inputs") or ()),
            weights=tuple(payload.get("weights") or ()),
            output_name=payload.get("output_name"),
        )


@dataclass(frozen=True)
class QuestionInterpretation:
    """A planner's (LLM or rule-based) reading of a natural-language
    question — everything *except* indicator codes, which only
    build_query_plan()'s catalog search may supply (see module docstring).
    """

    concepts: tuple[str, ...]
    geographies: tuple[str, ...] = ()
    start_period: str | None = None
    end_period: str | None = None
    frequency: str | None = None
    transformations: tuple[TransformationSpec, ...] = ()
    #: "cross_country" (one concept, several geographies) or
    #: "cross_indicator" (several concepts, one geography), or None for a
    #: single indicator/area question — mirrors core/compose.py's two
    #: comparison shapes so a later phase can route directly into them.
    comparison: str | None = None
    ranking: bool = False
    output_type: str = "table"
    #: Human-readable explanations of inferred choices (section 11: explain
    #: assumptions rather than silently picking one interpretation).
    assumptions: tuple[str, ...] = ()
    needs_clarification: bool = False
    clarification_question: str | None = None

    @staticmethod
    def from_dict(payload: dict) -> "QuestionInterpretation":
        return QuestionInterpretation(
            concepts=tuple(payload.get("concepts") or ()),
            geographies=tuple(payload.get("geographies") or ()),
            start_period=_clean_period(payload.get("start_period")),
            end_period=_clean_period(payload.get("end_period")),
            frequency=payload.get("frequency"),
            transformations=tuple(
                TransformationSpec.from_dict(t) for t in (payload.get("transformations") or ())
            ),
            comparison=payload.get("comparison"),
            ranking=bool(payload.get("ranking", False)),
            output_type=payload.get("output_type") or "table",
            assumptions=tuple(payload.get("assumptions") or ()),
            needs_clarification=bool(payload.get("needs_clarification", False)),
            clarification_question=payload.get("clarification_question"),
        )


@dataclass(frozen=True)
class QueryPlan:
    """The full, inspectable plan: a QuestionInterpretation plus the
    candidate indicators the catalog actually found for it."""

    question: str
    concepts: tuple[str, ...]
    candidate_indicators: tuple[CandidateIndicator, ...]
    geographies: tuple[str, ...] = ()
    start_period: str | None = None
    end_period: str | None = None
    frequency: str | None = None
    transformations: tuple[TransformationSpec, ...] = ()
    comparison: str | None = None
    ranking: bool = False
    output_type: str = "table"
    assumptions: tuple[str, ...] = ()
    needs_clarification: bool = False
    clarification_question: str | None = None
    #: Populated by source/indicator selection (Phase 8) — empty until then.
    selected_indicators: tuple[CandidateIndicator, ...] = ()
    #: Populated by the validation layer (Phase 9) — empty until then.
    validation_notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "concepts": list(self.concepts),
            "candidate_indicators": [c.as_dict() for c in self.candidate_indicators],
            "geographies": list(self.geographies),
            "start_period": self.start_period,
            "end_period": self.end_period,
            "frequency": self.frequency,
            "transformations": [t.as_dict() for t in self.transformations],
            "comparison": self.comparison,
            "ranking": self.ranking,
            "output_type": self.output_type,
            "assumptions": list(self.assumptions),
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.clarification_question,
            "selected_indicators": [c.as_dict() for c in self.selected_indicators],
            "validation_notes": list(self.validation_notes),
        }


def build_query_plan(
    question: str,
    interpretation: QuestionInterpretation,
    engine,
    *,
    candidates_per_concept: int = 5,
) -> QueryPlan:
    """Resolve an interpretation's concepts into real catalog candidates.

    Also resolves every concept referenced *inside* a transformation
    (TransformationSpec.concepts_referenced() — e.g. a `share`
    transformation's numerator_concept/denominator_concept) even if the
    planner didn't separately list it in `interpretation.concepts` — the
    planner only has to name a concept once, wherever it naturally belongs,
    not duplicate it into two places for the catalog to see it. `QueryPlan.
    concepts` (unlike `interpretation.concepts`) is the union of both, so
    core/selection.py's select_indicators() — which loops over
    `plan.concepts` — actually selects an indicator for a
    transformation-only concept too, not just top-level ones.

    Also resolves every geography to its canonical ISO 3166-1 alpha-3 form
    (core/geography.py) — a real LLM planner routinely writes a country
    NAME ("Azerbaijan") rather than the code a provider's ref_area needs
    (found live running AnthropicPlanner, Phase G), and the system prompt
    alone can't be trusted to make every model comply. Resolved once, here,
    so every downstream consumer (selection scoring's geographic_coverage
    check, column keys, validation, chart labels) sees the same canonical
    value; the source-specific code format (e.g. Eurostat's alpha-2) is
    applied only at the retrieval call boundary (core/ask.py::_fetch_table),
    not baked into the plan itself.

    `engine` is typed loosely (not core.engine.QueryEngine) to avoid a
    circular import — core/engine.py already imports providers that
    eventually need catalog/query_plan types; this only calls the one
    method every QueryEngine has (search_indicator), so a Protocol isn't
    worth the ceremony here.
    """
    all_concepts = list(interpretation.concepts)
    for transformation in interpretation.transformations:
        for concept in transformation.concepts_referenced():
            if concept not in all_concepts:
                all_concepts.append(concept)

    geographies = tuple(resolve_geography(g) for g in interpretation.geographies)
    # weighted_average's `inputs` are geography codes too (see
    # TransformationSpec's docstring) - same resolution, same reason.
    transformations = tuple(
        replace(t, inputs=tuple(resolve_geography(g) for g in t.inputs)) if t.inputs else t
        for t in interpretation.transformations
    )

    candidate_indicators = tuple(
        CandidateIndicator(
            indicator_id=match.indicator_id,
            source_id=match.source_id,
            name=match.name,
            concept=concept,
            unit=match.unit,
            frequency=match.frequency,
            geographic_coverage=match.geographic_coverage,
            semantics=match.semantics,
            search_rank=rank,
        )
        for concept in all_concepts
        for rank, match in enumerate(engine.search_indicator(concept, limit=candidates_per_concept))
    )

    return QueryPlan(
        question=question,
        concepts=tuple(all_concepts),
        candidate_indicators=candidate_indicators,
        geographies=geographies,
        start_period=interpretation.start_period,
        end_period=interpretation.end_period,
        frequency=interpretation.frequency,
        transformations=transformations,
        comparison=interpretation.comparison,
        ranking=interpretation.ranking,
        output_type=interpretation.output_type,
        assumptions=interpretation.assumptions,
        needs_clarification=interpretation.needs_clarification,
        clarification_question=interpretation.clarification_question,
    )

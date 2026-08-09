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

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CandidateIndicator:
    """One indicator the catalog matched for a requested concept — never
    proposed by an LLM, always the result of Catalog.search().

    Carries the same optional metadata IndicatorMeta does (unit, frequency,
    geographic_coverage) so source/indicator selection (core/selection.py,
    Phase 8) can score candidates without a second catalog lookup — this is
    exactly what engine.search_indicator() already returned in
    build_query_plan() below, just not previously kept.
    """

    indicator_id: str
    source_id: str
    name: str
    #: Which requested concept (QueryPlan.concepts) this candidate came from.
    concept: str
    unit: str | None = None
    frequency: str | None = None
    geographic_coverage: tuple[str, ...] | None = None

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
        }


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
    transformations: tuple[str, ...] = ()
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
            start_period=payload.get("start_period"),
            end_period=payload.get("end_period"),
            frequency=payload.get("frequency"),
            transformations=tuple(payload.get("transformations") or ()),
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
    transformations: tuple[str, ...] = ()
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
            "transformations": list(self.transformations),
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

    `engine` is typed loosely (not core.engine.QueryEngine) to avoid a
    circular import — core/engine.py already imports providers that
    eventually need catalog/query_plan types; this only calls the one
    method every QueryEngine has (search_indicator), so a Protocol isn't
    worth the ceremony here.
    """
    candidate_indicators = tuple(
        CandidateIndicator(
            indicator_id=match.indicator_id,
            source_id=match.source_id,
            name=match.name,
            concept=concept,
            unit=match.unit,
            frequency=match.frequency,
            geographic_coverage=match.geographic_coverage,
        )
        for concept in interpretation.concepts
        for match in engine.search_indicator(concept, limit=candidates_per_concept)
    )

    return QueryPlan(
        question=question,
        concepts=interpretation.concepts,
        candidate_indicators=candidate_indicators,
        geographies=interpretation.geographies,
        start_period=interpretation.start_period,
        end_period=interpretation.end_period,
        frequency=interpretation.frequency,
        transformations=interpretation.transformations,
        comparison=interpretation.comparison,
        ranking=interpretation.ranking,
        output_type=interpretation.output_type,
        assumptions=interpretation.assumptions,
        needs_clarification=interpretation.needs_clarification,
        clarification_question=interpretation.clarification_question,
    )

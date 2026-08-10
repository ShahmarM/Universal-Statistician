"""Structured query plan: the inspectable object between a question and
any retrieval. A planner proposes a QuestionInterpretation (concepts,
geographies, periods, transformations) but never an indicator code —
build_query_plan() fills in candidates via the deterministic catalog
search alone. A QueryPlan is data, not behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from universal_statistician.core.geography import resolve_geography
from universal_statistician.core.models import StatisticalSemantics

#: Words LLM planners have been observed writing instead of a null period
#: ("latest available year" -> end_period "latest"); normalized to None,
#: which every downstream consumer already treats as "not specified".
_NON_PERIOD_WORDS = {"latest", "present", "now", "current", "today", "n/a", "unknown", "none"}


def _clean_period(value: str | None) -> str | None:
    if value is None:
        return None
    if str(value).strip().lower() in _NON_PERIOD_WORDS:
        return None
    return value


@dataclass(frozen=True)
class CandidateIndicator:
    """One indicator the catalog matched for a requested concept — always
    the result of Catalog.search(), never proposed by an LLM. Carries
    IndicatorMeta's optional metadata so selection can score without a
    second lookup."""

    indicator_id: str
    source_id: str
    name: str
    #: Which requested concept this candidate came from.
    concept: str
    unit: str | None = None
    frequency: str | None = None
    geographic_coverage: tuple[str, ...] | None = None
    semantics: StatisticalSemantics | None = None
    #: 0-based position in the catalog search results for this concept —
    #: carries the catalog's own ranking into score_candidate(), which has
    #: no equivalent signal of its own.
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
    """One requested statistical operation — WHAT to compute, never a
    value. Pair operations carry natural-language *concepts* (resolved via
    the same catalog search as top-level concepts, never indicator codes).
    Exception: weighted_average's `inputs` are geography codes (it combines
    one concept across geographies already present in the plan). Simple
    operations set only `operation`."""

    operation: str
    numerator_concept: str | None = None
    denominator_concept: str | None = None
    input_concept: str | None = None
    base_period: str | None = None
    base_value: float | None = None
    left_concept: str | None = None
    right_concept: str | None = None
    #: weighted_average only: geography codes, 1:1 with `weights`.
    inputs: tuple[str, ...] = ()
    weights: tuple[float, ...] = ()
    #: Optional label for the derived column; a key is generated otherwise.
    output_name: str | None = None

    def concepts_referenced(self) -> tuple[str, ...]:
        """Concepts needing catalog resolution — never `inputs` (those are
        geography codes)."""
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
        # A bare operation name is still the natural way to write a simple,
        # no-concept operation.
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
    #: "cross_country", "cross_indicator", or None — mirrors compose.py's
    #: two comparison shapes.
    comparison: str | None = None
    ranking: bool = False
    output_type: str = "table"
    #: Human-readable explanations of inferred choices.
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
    #: Populated by select_indicators().
    selected_indicators: tuple[CandidateIndicator, ...] = ()
    #: Populated by the validation layer.
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
    """Resolve concepts into real catalog candidates. QueryPlan.concepts
    is the union of top-level and transformation-referenced concepts, so a
    concept named only inside a transformation still gets selected.
    Geographies are canonicalized to alpha-3 once, here — LLM planners
    routinely write country names; source-specific code formats apply only
    at the retrieval boundary. `engine` is untyped to avoid a circular
    import; only search_indicator() is called."""
    all_concepts = list(interpretation.concepts)
    for transformation in interpretation.transformations:
        for concept in transformation.concepts_referenced():
            if concept not in all_concepts:
                all_concepts.append(concept)

    geographies = tuple(resolve_geography(g) for g in interpretation.geographies)
    # weighted_average's `inputs` are geography codes — same resolution.
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

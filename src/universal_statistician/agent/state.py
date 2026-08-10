"""InvestigationState: per-question working memory for the agent tool layer.

Scoped to one investigation; nothing persists between requests. Holds real
evidence (`table`, `retrieved`, `derived` — one column per retrieval or
calculation, keyed by stable result_ids the LLM must reference) and an
audit trail (candidates, warnings, tool_call_history, ...) that is only
ever appended to, never used to compute anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from universal_statistician.agent.evidence import EvidenceEntry, build_evidence_index
from universal_statistician.core.compose import ComparisonColumn, ComparisonTable
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import SeriesResult


def catalog_id(source_id: str, indicator_id: str) -> str:
    """Stable identity later tool calls reference — copied verbatim from a
    prior tool result, never constructed by the LLM."""
    return f"{source_id}::{indicator_id}"


def parse_catalog_id(value: str) -> tuple[str, str]:
    try:
        source_id, indicator_id = value.split("::", 1)
    except ValueError:
        raise ValueError(
            f"Malformed catalog_id {value!r}: expected 'source_id::indicator_id' "
            "(exactly as returned by search_series/inspect_series)."
        ) from None
    return source_id, indicator_id


@dataclass(frozen=True)
class CandidateSummary:
    """One catalog candidate as surfaced to the LLM — never a selection."""

    catalog_id: str
    source_id: str
    indicator_id: str
    title: str
    organization: str | None
    dataset_id: str | None
    unit: str | None
    frequency: str | None
    price_basis: str | None
    seasonally_adjusted: bool | None
    geographic_coverage: tuple[str, ...] | None
    official_url: str | None
    search_rank: int
    search_score_note: str

    def as_dict(self) -> dict:
        return {
            "catalog_id": self.catalog_id,
            "source_id": self.source_id,
            "indicator_id": self.indicator_id,
            "title": self.title,
            "organization": self.organization,
            "dataset_id": self.dataset_id,
            "unit": self.unit,
            "frequency": self.frequency,
            "price_basis": self.price_basis,
            "seasonally_adjusted": self.seasonally_adjusted,
            "geographic_coverage": (
                list(self.geographic_coverage) if self.geographic_coverage is not None else None
            ),
            "official_url": self.official_url,
            "search_rank": self.search_rank,
            "search_score_note": self.search_score_note,
        }


@dataclass(frozen=True)
class RejectedCandidate:
    catalog_id: str
    reason: str

    def as_dict(self) -> dict:
        return {"catalog_id": self.catalog_id, "reason": self.reason}


@dataclass(frozen=True)
class RetrievedResult:
    """One retrieve_series() outcome; keeps the raw SeriesResult for tools
    that need untouched observations, not just flattened table cells."""

    result_id: str
    catalog_id: str
    ref_area: str
    series: SeriesResult


@dataclass(frozen=True)
class DerivedResult:
    """One calculate() call's outcome."""

    result_id: str
    operation: str
    formula: str
    input_result_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCallRecord:
    iteration: int
    tool_name: str
    input: dict
    #: Structured summary, not the full tool-result text — small and safe
    #: to serialize into debug/observability output.
    output_summary: dict
    duration_ms: float

    def as_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "tool_name": self.tool_name,
            "input": self.input,
            "output_summary": self.output_summary,
            "duration_ms": self.duration_ms,
        }


@dataclass
class InvestigationState:
    question: str
    engine: QueryEngine

    #: Accumulated evidence — grows one column per retrieve_series/calculate
    #: call. Never mutated in place by anything except agent/tools.py.
    table: ComparisonTable = field(
        default_factory=lambda: ComparisonTable(columns=(), values={})
    )
    retrieved: dict[str, RetrievedResult] = field(default_factory=dict)
    derived: dict[str, DerivedResult] = field(default_factory=dict)

    #: Audit trail — append-only, never read by tools to decide behavior.
    candidates_considered: list[CandidateSummary] = field(default_factory=list)
    candidates_rejected: list[RejectedCandidate] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unresolved_ambiguities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_results: list[dict] = field(default_factory=list)
    #: One VerificationReport.as_dict() per verifier round — debug-only;
    #: read by agent/modes.py's retry loop, never fed back into evidence.
    verification_results: list[dict] = field(default_factory=list)
    provenance_references: list[dict] = field(default_factory=list)
    tool_call_history: list[ToolCallRecord] = field(default_factory=list)
    iteration_count: int = 0
    #: Actual provider requests (one per geography tried, success or fail)
    #: — distinct from tool-call count; AgentLimits.max_provider_calls
    #: bounds this so one call requesting 20 countries can't bypass it.
    provider_call_count: int = 0
    #: Investigator's own final free-text turn — debug-only, never the
    #: user-facing answer.
    investigator_summary: str = ""

    _next_result_id: int = field(default=1, repr=False)

    def new_result_id(self) -> str:
        result_id = f"result_{self._next_result_id}"
        self._next_result_id += 1
        return result_id

    def add_column(self, column: ComparisonColumn, values: dict[str, float | None]) -> None:
        """Append one column (and its period->value cells) to `table`.
        `values` maps period -> value for this column only."""
        self.table = ComparisonTable(
            columns=(*self.table.columns, column),
            values={
                **self.table.values,
                **{(period, column.key): value for period, value in values.items()},
            },
            cell_dependencies=dict(self.table.cell_dependencies),
        )

    def merge_derived_table(self, updated_table: ComparisonTable, new_column_key: str) -> None:
        """Absorb the one new derived column a with_*() call added to a
        scratch table built from this table's columns."""
        new_column = updated_table.column(new_column_key)
        self.table = ComparisonTable(
            columns=(*self.table.columns, new_column),
            values={
                **self.table.values,
                **{
                    (period, new_column_key): updated_table.value_at(period, new_column_key)
                    for period in updated_table.periods()
                },
            },
            cell_dependencies={
                **self.table.cell_dependencies,
                **{
                    key: deps
                    for key, deps in updated_table.cell_dependencies.items()
                    if key[1] == new_column_key
                },
            },
        )

    def evidence_index(self) -> dict[str, EvidenceEntry]:
        """evidence_id -> EvidenceEntry for every populated cell, rebuilt
        fresh each call so it can't go stale."""
        return build_evidence_index(self)

    def resolve_result_ids(self) -> dict:
        """Debug view of every result_id produced, base and derived."""
        return {
            "retrieved": {rid: r.catalog_id for rid, r in self.retrieved.items()},
            "derived": {
                rid: {"operation": d.operation, "inputs": list(d.input_result_ids)}
                for rid, d in self.derived.items()
            },
        }

    def evidence_package(self) -> dict:
        """Everything downstream (answer writer, verifier) may use, and only
        that — excludes tool_call_history payloads and the investigator's
        free text."""
        return {
            "question": self.question,
            "table": self.table.as_dict(),
            "evidence": {eid: entry.as_dict() for eid, entry in self.evidence_index().items()},
            "candidates_considered": [c.as_dict() for c in self.candidates_considered],
            "candidates_rejected": [c.as_dict() for c in self.candidates_rejected],
            "result_ids": self.resolve_result_ids(),
            "assumptions": list(self.assumptions),
            "unresolved_ambiguities": list(self.unresolved_ambiguities),
            "warnings": list(self.warnings),
            "validation_results": list(self.validation_results),
            "provenance_references": list(self.provenance_references),
        }

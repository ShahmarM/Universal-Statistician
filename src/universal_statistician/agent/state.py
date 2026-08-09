"""InvestigationState: the per-question working memory the agent tool layer
(agent/tools.py) reads from and writes to.

Deliberately scoped to one investigation, not a long-term memory store —
"the purpose is to prevent repeated searches and to make the investigation
auditable," not to remember anything across questions. A caller building a
new StatisticalAgent run constructs a fresh InvestigationState; nothing
here is persisted between requests.

Two kinds of data live here:

1. **Real evidence** (`table`, `retrieved`, `derived`): a `ComparisonTable`
   (core/compose.py) that accumulates one column per retrieved series and
   one column per calculation, keyed by a stable `result_id` the LLM
   references in later tool calls (never a raw number, never an indicator
   code it invented — see agent/tools.py's module docstring). This is
   exactly the structure core/ask.py's legacy path already builds, just
   grown incrementally instead of all at once from a pre-computed plan.
2. **Audit trail** (`candidates_considered`, `candidates_rejected`,
   `tool_call_history`, `assumptions`, `unresolved_ambiguities`,
   `warnings`, `validation_results`, `provenance_references`,
   `iteration_count`): never used to compute anything, only to make the
   investigation inspectable — the `debug=true` /ask response (task
   section 15) and the observability log (section 21) are both built from
   this, not from re-deriving it after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from universal_statistician.core.compose import ComparisonColumn, ComparisonTable
from universal_statistician.core.engine import QueryEngine
from universal_statistician.core.models import SeriesResult


def catalog_id(source_id: str, indicator_id: str) -> str:
    """The stable string identity a search/inspect result is referenced by
    in later tool calls — never guessed or constructed by the LLM itself,
    always copied verbatim from a prior tool result."""
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
    """One catalog candidate as surfaced to the LLM — a search/inspect
    result, never a selection. See agent/tools.py::search_series/
    inspect_series."""

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
    """One retrieve_series() call's outcome — the ComparisonColumn it added
    to `InvestigationState.table` plus the raw SeriesResult it came from
    (kept for compare_series/inspect_provenance, which need the untouched
    observations, not only the table's flattened period->value cells)."""

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
    #: Structured, not the full tool-result text — kept small and safe to
    #: serialize into the debug response / observability log (section 21:
    #: never log full hidden reasoning, only structured decisions).
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

    #: Audit trail (see module docstring) — never read by any tool to
    #: decide behavior, only appended to.
    candidates_considered: list[CandidateSummary] = field(default_factory=list)
    candidates_rejected: list[RejectedCandidate] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unresolved_ambiguities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_results: list[dict] = field(default_factory=list)
    provenance_references: list[dict] = field(default_factory=list)
    tool_call_history: list[ToolCallRecord] = field(default_factory=list)
    iteration_count: int = 0

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
        """Absorb the single new derived column a compose.py with_*()
        function added to a scratch table built from `self.table`'s
        existing columns — see agent/tools.py::calculate()."""
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

    def resolve_result_ids(self) -> dict:
        """Debug/summary view: every result_id this investigation has
        produced, base and derived alike — task section 15's `debug=true`
        "selected_series"/"calculations" and section 3's audit fields."""
        return {
            "retrieved": {rid: r.catalog_id for rid, r in self.retrieved.items()},
            "derived": {
                rid: {"operation": d.operation, "inputs": list(d.input_result_ids)}
                for rid, d in self.derived.items()
            },
        }

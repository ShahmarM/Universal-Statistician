"""The answer-result model (section 18/20/21): what /ask (Phase 11's
endpoint, api.py) returns, and a chart specification separate from any
particular charting library.

Deliberately data, not rendering: ChartSpec names series by the same column
keys ComparisonTable already uses, so the frontend (Phase 12) draws it with
the charting approach already in place (recharts), not a new one, and
`table` stays the structured ComparisonTable.as_dict() shape (section 19:
never only a formatted markdown string).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChartSeries:
    key: str
    label: str


@dataclass(frozen=True)
class ChartSpec:
    chart_type: str  # "line" | "bar" | "comparison"
    title: str
    x_axis: str
    y_axis: str
    series: tuple[ChartSeries, ...]
    source_note: str
    subtitle: str | None = None
    units: str | None = None

    def as_dict(self) -> dict:
        return {
            "chart_type": self.chart_type,
            "title": self.title,
            "subtitle": self.subtitle,
            "x_axis": self.x_axis,
            "y_axis": self.y_axis,
            "units": self.units,
            "series": [{"key": s.key, "label": s.label} for s in self.series],
            "source_note": self.source_note,
        }


@dataclass(frozen=True)
class AskResult:
    question: str
    #: QueryPlan.as_dict() — interpretation + candidate/selected indicators,
    #: inspectable/debuggable per section 10.
    query_plan: dict
    #: Deterministic, template-built from `table`/`validation` only — never
    #: independently rewritten by an LLM (section 18).
    answer: str
    #: ComparisonTable.as_dict(), or None if nothing could be retrieved.
    table: dict | None
    chart: dict | None
    #: Deduplicated Attribution dicts for every base column actually used.
    sources: tuple[dict, ...]
    #: Provenance (core/provenance.py) for the latest-period value of every
    #: column — base and derived alike.
    provenance: tuple[dict, ...]
    warnings: tuple[str, ...]
    #: ValidationResult.as_dict(), or None if validation didn't run (e.g.
    #: retrieval never happened because the plan needed clarification).
    validation: dict | None

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "query_plan": self.query_plan,
            "answer": self.answer,
            "table": self.table,
            "chart": self.chart,
            "sources": list(self.sources),
            "provenance": list(self.provenance),
            "warnings": list(self.warnings),
            "validation": self.validation,
        }

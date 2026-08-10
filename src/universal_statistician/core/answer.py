"""The answer-result model /ask returns, plus a charting-library-agnostic
chart spec. Data, not rendering: ChartSpec names series by ComparisonTable's
own column keys, and `table` stays structured, never a markdown string.
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
    #: QueryPlan.as_dict() (or the agent's evidence package in research
    #: mode) — the inspectable record of how the answer was reached.
    query_plan: dict
    #: Built from `table`/`validation`, or by the grounding-checked answer
    #: writer — never an LLM independently rewriting numbers.
    answer: str
    #: ComparisonTable.as_dict(), or None if nothing could be retrieved.
    table: dict | None
    chart: dict | None
    #: Deduplicated Attribution dicts for every base column used.
    sources: tuple[dict, ...]
    #: Provenance for each column's latest-period value, base and derived.
    provenance: tuple[dict, ...]
    warnings: tuple[str, ...]
    #: ValidationResult.as_dict(), or None if validation didn't run.
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

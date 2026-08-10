"""Provenance/citation system: resolves any table cell — base or derived,
at any depth — into a traceable chain back to official observations.

Exact by construction: cell_dependencies records the precise input cells
each derived cell was computed from, at compute time. A derived column
without such an entry (built outside compose.py's with_*() functions)
falls back to a column-level chain with an explicit imprecision `note` —
never a specific-looking but possibly-wrong single period.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from universal_statistician.core.compose import ComparisonColumn, ComparisonTable

CALCULATED_BY = "Universal Statistician"


@dataclass(frozen=True)
class ObservationProvenance:
    column_key: str
    indicator_id: str | None
    ref_area: str | None
    period: str
    value: float | None
    source_id: str
    source_name: str
    dataset_id: str
    unit: str | None
    frequency: str | None
    official_url: str | None
    retrieved_at: str
    kind: str = "observation"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "column_key": self.column_key,
            "indicator_id": self.indicator_id,
            "ref_area": self.ref_area,
            "period": self.period,
            "value": self.value,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "dataset_id": self.dataset_id,
            "unit": self.unit,
            "frequency": self.frequency,
            "official_url": self.official_url,
            "retrieved_at": self.retrieved_at,
        }


@dataclass(frozen=True)
class DerivedProvenance:
    column_key: str
    period: str
    value: float | None
    formula: str
    calculated_at: str
    inputs: tuple["Provenance", ...]
    calculated_by: str = CALCULATED_BY
    #: Set when an input's exact contributing period couldn't be pinned down
    #: (see module docstring) — None when resolution was exact.
    note: str | None = None
    kind: str = "derived"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "column_key": self.column_key,
            "period": self.period,
            "value": self.value,
            "formula": self.formula,
            "calculated_by": self.calculated_by,
            "calculated_at": self.calculated_at,
            "note": self.note,
            "inputs": [i.as_dict() for i in self.inputs],
        }


Provenance = ObservationProvenance | DerivedProvenance

#: Transformations depending on more than one period per input, recognized
#: by column-key suffix (no structured flag exists yet).
_MULTI_PERIOD_SUFFIXES = (
    "__yoy_growth_pct",
    "__period_over_period_growth_pct",
    "__abs_change",
    "__pp_change",
    "__cagr_pct",
    "__cumulative_growth_pct",
)


def _is_multi_period(column: ComparisonColumn) -> bool:
    if any(column.key.endswith(suffix) for suffix in _MULTI_PERIOD_SUFFIXES):
        return True
    return "__ma" in column.key  # with_moving_average's with_moving_average(window) key


def _observation_provenance(
    table: ComparisonTable, column: ComparisonColumn, period: str
) -> ObservationProvenance:
    attribution = column.attribution
    if attribution is None:
        raise ValueError(
            f"Cannot build provenance for base column {column.key!r}: no attribution "
            "recorded (see core/validation.py's citations_exist check, which would "
            "already have flagged this as a FAIL)."
        )
    return ObservationProvenance(
        column_key=column.key,
        indicator_id=column.indicator_id,
        ref_area=column.ref_area,
        period=period,
        value=table.value_at(period, column.key),
        source_id=attribution.source_id,
        source_name=attribution.source_name,
        dataset_id=attribution.dataset_id,
        unit=column.unit,
        frequency=column.frequency,
        official_url=attribution.source_url,
        retrieved_at=attribution.retrieved_at.isoformat(),
    )


def resolve_provenance(
    table: ComparisonTable, column_key: str, period: str, *, calculated_at: str | None = None
) -> Provenance:
    """Resolve one cell's full provenance chain."""
    column = table.column(column_key)

    if not column.derived:
        return _observation_provenance(table, column, period)

    calculated_at = calculated_at or datetime.now(timezone.utc).isoformat()
    inputs: list[Provenance] = []
    note: str | None = None

    exact_dependencies = table.cell_dependencies.get((period, column_key))
    if exact_dependencies is not None:
        for input_key, input_period in exact_dependencies:
            inputs.append(
                resolve_provenance(table, input_key, input_period, calculated_at=calculated_at)
            )
    else:
        # Column-level fallback for a derived column built outside
        # compose.py's with_*() functions (see module docstring).
        for input_key in column.input_series or ():
            if not _is_multi_period(column) and table.value_at(period, input_key) is not None:
                inputs.append(
                    resolve_provenance(table, input_key, period, calculated_at=calculated_at)
                )
                continue

            note = (
                "This formula depends on more than one period of at least one input; "
                "provenance below lists every period that input has a value for, "
                "not only the period of the derived value above."
            )
            for other_period in sorted(
                p for p in table.periods() if table.value_at(p, input_key) is not None
            ):
                inputs.append(
                    resolve_provenance(table, input_key, other_period, calculated_at=calculated_at)
                )

    return DerivedProvenance(
        column_key=column.key,
        period=period,
        value=table.value_at(period, column.key),
        formula=column.formula or "(formula not recorded)",
        calculated_at=calculated_at,
        inputs=tuple(inputs),
        note=note,
    )

"""Stable per-cell evidence identity.

Every populated table cell gets one EvidenceEntry keyed by
`evidence_id = "{result_id}@{period}"`, rebuilt fresh from the table
(cell_dependencies supplies exact derived-cell inputs). A claim must cite
a specific cell whose real geography/period/value_kind Python then checks
— so "Georgia GDP was 72.4" fails when 72.4's cell belongs to Azerbaijan,
which a flat "number exists somewhere" check could never catch.
value_kind comes only from real signals (producing operation, unit
string); "unknown" otherwise, never a guess.

Limitation: this checks a citation's declared metadata, not the prose
around it; agent/verifier.py's semantic pass covers that residual case.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Closed set; "unknown" is legitimate (nothing pinned the kind down).
VALUE_KINDS = (
    "count",
    "currency",
    "index",
    "level",
    "percent",
    "percentage_points",
    "ratio",
    "rank",
    "unknown",
)

#: Operations whose result kind is fixed regardless of input kind.
_FIXED_OPERATION_KINDS: dict[str, str] = {
    "growth": "percent",
    "yoy_growth": "percent",
    "period_over_period_growth": "percent",
    "cagr": "percent",
    "cumulative_growth": "percent",
    "share": "percent",
    "pp_change": "percentage_points",
    "index": "index",
    "rank": "rank",
    "per_capita": "ratio",
}

#: Operations whose result kind matches their input's; propagated from the
#: first input's entry when known.
_KIND_PRESERVING_OPS = frozenset(
    {"absolute_change", "difference", "sum", "average", "weighted_average", "moving_average"}
)


def _infer_base_value_kind(unit: str | None) -> str:
    """Classify a base cell's value_kind from its unit string. "level" only
    when a unit string was present but matched nothing; no unit at all
    stays "unknown"."""
    if not unit:
        return "unknown"
    lowered = unit.lower()
    if "%" in lowered or "percent" in lowered:
        return "percent"
    if "index" in lowered:
        return "index"
    if any(
        token in lowered
        for token in ("us$", "usd", "eur", "gbp", "currency", "national currency", "$", "€", "£")
    ):
        return "currency"
    if "person" in lowered or lowered.strip() == "count" or "count of" in lowered:
        return "count"
    return "level"


@dataclass(frozen=True)
class EvidenceEntry:
    """One numeric fact this investigation can cite — one table cell."""

    evidence_id: str
    result_id: str
    period: str
    value: float
    unit: str | None
    #: One of VALUE_KINDS.
    value_kind: str
    #: Base (retrieved) fields — None for a derived entry.
    indicator_id: str | None = None
    geography: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    dataset_id: str | None = None
    #: Derived fields — None for a base entry.
    operation: str | None = None
    formula: str | None = None
    input_evidence_ids: tuple[str, ...] = ()

    @property
    def derived(self) -> bool:
        return self.operation is not None

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "result_id": self.result_id,
            "period": self.period,
            "value": self.value,
            "unit": self.unit,
            "value_kind": self.value_kind,
            "indicator_id": self.indicator_id,
            "geography": self.geography,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "dataset_id": self.dataset_id,
            "operation": self.operation,
            "formula": self.formula,
            "input_evidence_ids": list(self.input_evidence_ids),
        }


def build_evidence_index(state) -> dict[str, EvidenceEntry]:
    """Every populated cell in `state.table`, as evidence_id -> EvidenceEntry.
    Rebuilt fresh every call so it can't drift from the table. `state` is
    InvestigationState (untyped to avoid a circular import)."""
    table = state.table
    periods = table.periods()
    index: dict[str, EvidenceEntry] = {}
    for column in table.columns:
        if not column.derived:
            attribution = column.attribution
            base_kind = _infer_base_value_kind(column.unit)
            for period in periods:
                value = table.value_at(period, column.key)
                if value is None:
                    continue
                evidence_id = f"{column.key}@{period}"
                index[evidence_id] = EvidenceEntry(
                    evidence_id=evidence_id,
                    result_id=column.key,
                    period=period,
                    value=value,
                    unit=column.unit,
                    value_kind=base_kind,
                    indicator_id=column.indicator_id,
                    geography=column.ref_area,
                    source_id=attribution.source_id if attribution else None,
                    source_name=attribution.source_name if attribution else None,
                    dataset_id=attribution.dataset_id if attribution else None,
                )
            continue

        derived_result = state.derived.get(column.key)
        operation = derived_result.operation if derived_result else None
        formula = derived_result.formula if derived_result else column.formula
        for period in periods:
            value = table.value_at(period, column.key)
            if value is None:
                continue
            evidence_id = f"{column.key}@{period}"
            dependencies = table.cell_dependencies.get((period, column.key), ())
            input_evidence_ids = tuple(f"{key}@{dep_period}" for key, dep_period in dependencies)

            if operation in _FIXED_OPERATION_KINDS:
                value_kind = _FIXED_OPERATION_KINDS[operation]
            elif operation in _KIND_PRESERVING_OPS and input_evidence_ids and input_evidence_ids[0] in index:
                value_kind = index[input_evidence_ids[0]].value_kind
            else:
                value_kind = "unknown"

            index[evidence_id] = EvidenceEntry(
                evidence_id=evidence_id,
                result_id=column.key,
                period=period,
                value=value,
                unit=column.unit,
                value_kind=value_kind,
                operation=operation,
                formula=formula,
                input_evidence_ids=input_evidence_ids,
            )
    return index

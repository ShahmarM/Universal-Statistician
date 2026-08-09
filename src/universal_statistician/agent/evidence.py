"""Stable per-cell evidence identity (task section 2).

Replaces "does this number exist somewhere in the evidence" (the original
Phase 6 numeric-consistency guard) with "does this number correspond to a
*specific* table cell, and does the claim's declared geography/period/kind
actually match that cell's" — the "Georgia GDP was 72.4" must fail if 72.4
belongs to Azerbaijan requirement. A flat existence check can never catch
that (72.4 genuinely is somewhere in the evidence); a per-cell identity
that a claim must cite, and that Python checks the claim's declared
geography/period/value_kind against, can.

Every populated table cell (base or derived, any period) gets exactly one
`EvidenceEntry`, keyed by `evidence_id = "{result_id}@{period}"` — never
re-derived by two different code paths, so it can't drift: `build_evidence_index()`
reads `InvestigationState.table` (specifically `ComparisonTable.
cell_dependencies`, Phase E's exact per-cell provenance) fresh every time,
the same source of truth core/provenance.py's `resolve_provenance()`
already uses for the human-readable citation tree. A derived entry's
`input_evidence_ids` are the *exact* cells that specific derived value was
computed from — not "some period of the input column" — because
cell_dependencies already records that precisely; this module only needs
to re-key it as `"{key}@{period}"` strings, not re-derive it.

`value_kind` distinguishes percent / percentage_points / currency / index /
count / ratio / rank / level, so a claim can't pass by matching a number
that's numerically equal but semantically a different kind of quantity
(e.g. citing a currency-level cell for a claim declared as a percentage).
Inferred only from real signals (the operation that produced a derived
cell, or a base cell's unit string) — "unknown" whenever neither pins it
down, never a guess.

Honest limitation, stated once here rather than left implicit: this module
checks a *citation's declared* geography/period/value_kind against the
evidence it points to — it cannot verify that the prose sentence around a
number actually agrees with what the citation declares (that would need
real NLP over free text, out of scope for a deterministic Python checker).
agent/answer_writer.py's forced-citation schema is the mitigation: making
the model state geography/period/kind as structured fields it must get
right, rather than trusting free prose, is a much stronger forcing
function than nothing, but agent/verifier.py's independent LLM semantic
pass (geography_mismatch/period_mismatch categories) remains the second,
complementary layer for the residual "prose disagrees with its own
citation" case this module structurally cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Closed set — every EvidenceEntry.value_kind is one of these.
#: "unknown" is a legitimate value (not an error): it means neither the
#: producing operation nor the unit string pinned down the kind, and
#: nothing here guesses further.
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

#: Operations (agent/expressions.py::CALCULATE_OPERATIONS) whose result is
#: always this kind, regardless of the input's own kind — growth-type
#: operations always produce a percent even from a currency-level input,
#: `index` always produces an index, etc.
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

#: Operations whose result is the same *kind* of quantity as their input
#: (a change/sum/average of a currency level is still a currency level) —
#: value_kind is propagated from the first input's own evidence entry
#: rather than guessed, only when that input is itself already known.
_KIND_PRESERVING_OPS = frozenset(
    {"absolute_change", "difference", "sum", "average", "weighted_average", "moving_average"}
)


def _infer_base_value_kind(unit: str | None) -> str:
    """Best-effort classification of a *base* (retrieved, not derived)
    cell's value_kind from its unit string alone — real signal, not a
    guess: every branch here matches an actual substring a real provider's
    unit text would contain. Falls back to "level" (a plain quantity,
    neither a percent/index/count/currency) rather than "unknown" only
    because *some* unit string was actually present; genuinely absent unit
    metadata stays "unknown" (see caller)."""
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
    Rebuilt fresh from the table every call — never incrementally
    maintained elsewhere, so it can never drift out of sync with it.
    `state` is agent/state.py's InvestigationState (not type-hinted
    directly to avoid a circular import; state.py imports this module)."""
    table = state.table
    index: dict[str, EvidenceEntry] = {}
    for column in table.columns:
        for period in table.periods():
            value = table.value_at(period, column.key)
            if value is None:
                continue
            evidence_id = f"{column.key}@{period}"

            if not column.derived:
                attribution = column.attribution
                index[evidence_id] = EvidenceEntry(
                    evidence_id=evidence_id,
                    result_id=column.key,
                    period=period,
                    value=value,
                    unit=column.unit,
                    value_kind=_infer_base_value_kind(column.unit),
                    indicator_id=column.indicator_id,
                    geography=column.ref_area,
                    source_id=attribution.source_id if attribution else None,
                    source_name=attribution.source_name if attribution else None,
                    dataset_id=attribution.dataset_id if attribution else None,
                )
                continue

            dependencies = table.cell_dependencies.get((period, column.key), ())
            input_evidence_ids = tuple(f"{key}@{dep_period}" for key, dep_period in dependencies)
            derived_result = state.derived.get(column.key)
            operation = derived_result.operation if derived_result else None
            formula = derived_result.formula if derived_result else column.formula

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

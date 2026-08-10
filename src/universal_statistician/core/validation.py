"""Validation layer: structured PASS/WARNING/FAIL findings after
retrieval/calculation, before an answer is built. A FAIL means "do not
present this as a supported numerical answer."

validate_series() checks a fresh SeriesResult (duplicates, NaN, empty);
validate_table() checks a ComparisonTable (citations, consistency,
coverage, derived lineage). Unknown metadata is never treated as a
contradiction — only two *known*, differing values are flagged.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from universal_statistician.core.compose import ComparisonTable
from universal_statistician.core.geography import resolve_geography
from universal_statistician.core.models import SeriesResult


class ValidationStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


_SEVERITY = {ValidationStatus.PASS: 0, ValidationStatus.WARNING: 1, ValidationStatus.FAIL: 2}


@dataclass(frozen=True)
class ValidationFinding:
    status: ValidationStatus
    #: Stable check name, e.g. "citations_exist" — callers filter by it.
    check: str
    message: str

    def as_dict(self) -> dict:
        return {"status": self.status.value, "check": self.check, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    findings: tuple[ValidationFinding, ...]

    @property
    def status(self) -> ValidationStatus:
        if not self.findings:
            return ValidationStatus.PASS
        return max(self.findings, key=lambda f: _SEVERITY[f.status]).status

    @property
    def ok(self) -> bool:
        """False means FAIL — check before presenting derived numbers."""
        return self.status != ValidationStatus.FAIL

    def as_dict(self) -> dict:
        return {"status": self.status.value, "findings": [f.as_dict() for f in self.findings]}


def _is_nan(value: float | None) -> bool:
    return value is not None and isinstance(value, float) and math.isnan(value)


def validate_series(series: SeriesResult) -> ValidationResult:
    """Checks only visible before observations flatten into a table:
    duplicate periods, NaN values that should be None, empty results."""
    findings: list[ValidationFinding] = []
    label = f"{series.indicator_id}/{series.ref_area}"

    period_counts = Counter(o.period for o in series.observations)
    duplicate_periods = sorted(p for p, count in period_counts.items() if count > 1)
    if duplicate_periods:
        findings.append(
            ValidationFinding(
                ValidationStatus.WARNING,
                "duplicate_observations",
                f"{label}: duplicate periods in the raw series: {duplicate_periods}",
            )
        )

    nan_periods = [o.period for o in series.observations if _is_nan(o.value)]
    if nan_periods:
        findings.append(
            ValidationFinding(
                ValidationStatus.FAIL,
                "nan_value",
                f"{label}: NaN value(s) at {nan_periods} — providers must normalize "
                "missing values to None, never leave a NaN float in a result",
            )
        )

    if not series.observations:
        findings.append(
            ValidationFinding(
                ValidationStatus.WARNING, "no_observations", f"{label}: no observations returned"
            )
        )

    if not findings:
        findings.append(ValidationFinding(ValidationStatus.PASS, "series", f"{label}: no issues found"))

    return ValidationResult(tuple(findings))


def validate_table(
    table: ComparisonTable,
    *,
    requested_geographies: tuple[str, ...] = (),
    requested_start_period: str | None = None,
    requested_end_period: str | None = None,
) -> ValidationResult:
    findings: list[ValidationFinding] = []
    base_columns = [c for c in table.columns if not c.derived]
    derived_columns = [c for c in table.columns if c.derived]
    known_keys = {c.key for c in table.columns}

    # Citations exist for every base series: no number without a source.
    for c in base_columns:
        if c.attribution is None:
            findings.append(
                ValidationFinding(
                    ValidationStatus.FAIL,
                    "citations_exist",
                    f"Base column {c.key!r} has no attribution — cannot cite an official source for it",
                )
            )

    # Unit/frequency consistency — only when both values are known and differ.
    known_frequencies = sorted({c.frequency for c in base_columns if c.frequency})
    if len(known_frequencies) > 1:
        findings.append(
            ValidationFinding(
                ValidationStatus.WARNING,
                "frequency_consistency",
                f"Base columns have differing known frequencies: {known_frequencies} — "
                "comparing them directly may not be meaningful without resampling.",
            )
        )
    known_units = sorted({c.unit for c in base_columns if c.unit})
    if len(known_units) > 1:
        findings.append(
            ValidationFinding(
                ValidationStatus.WARNING,
                "unit_consistency",
                f"Base columns have differing known units: {known_units} — "
                "comparing them directly may not be meaningful without conversion.",
            )
        )

    # Price-basis consistency — same "unknown is never a contradiction" rule.
    known_price_bases = sorted(
        {
            c.semantics.price_basis
            for c in base_columns
            if c.semantics is not None and c.semantics.price_basis
        }
    )
    if len(known_price_bases) > 1:
        findings.append(
            ValidationFinding(
                ValidationStatus.WARNING,
                "price_basis_consistency",
                f"Base columns have differing known price bases: {known_price_bases} — "
                "e.g. combining nominal and real values directly is usually not meaningful.",
            )
        )

    # Requested geographies actually returned. Matched on ref_area, never
    # `key` (agent columns are keyed by opaque result_ids), and both sides
    # resolved through resolve_geography() — requests may carry a country
    # name ("Georgia") while ref_area is the ISO code ("GEO").
    resolved_columns = [
        (c, resolve_geography(c.ref_area).upper()) for c in base_columns if c.ref_area is not None
    ]
    for geo in requested_geographies:
        resolved_geo = resolve_geography(geo).upper()
        col = next((c for c, resolved in resolved_columns if resolved == resolved_geo), None)
        if col is None:
            findings.append(
                ValidationFinding(
                    ValidationStatus.WARNING,
                    "requested_geographies_returned",
                    f"Requested geography {geo!r} has no corresponding column in the result",
                )
            )
        elif not any(table.value_at(p, col.key) is not None for p in table.periods()):
            findings.append(
                ValidationFinding(
                    ValidationStatus.WARNING,
                    "requested_geographies_returned",
                    f"Requested geography {geo!r} has a column but no non-null observations",
                )
            )

    # requested period range actually covered
    if requested_start_period or requested_end_period:
        for c in base_columns:
            covered = [
                p
                for p in table.periods()
                if table.value_at(p, c.key) is not None
                and (requested_start_period is None or p >= requested_start_period)
                and (requested_end_period is None or p <= requested_end_period)
            ]
            if not covered:
                findings.append(
                    ValidationFinding(
                        ValidationStatus.WARNING,
                        "requested_periods_returned",
                        f"Column {c.key!r} has no observations within the requested period range",
                    )
                )

    # Unexpected gaps — only for columns whose every period is a plain
    # 4-digit year, so this never misfires on monthly/quarterly data.
    for c in base_columns:
        periods_with_values = sorted(p for p in table.periods() if table.value_at(p, c.key) is not None)
        if len(periods_with_values) < 2:
            continue
        if not all(len(p) == 4 and p.isdigit() for p in periods_with_values):
            continue
        years = [int(p) for p in periods_with_values]
        gaps = [(years[i], years[i + 1]) for i in range(len(years) - 1) if years[i + 1] - years[i] > 1]
        if gaps:
            findings.append(
                ValidationFinding(
                    ValidationStatus.WARNING,
                    "no_unexpected_gaps",
                    f"Column {c.key!r} has gap(s) in its annual coverage: {gaps}",
                )
            )

    # Derived-column lineage integrity: every derived column must record
    # its inputs, and every referenced input must exist in this table.
    for c in derived_columns:
        if not c.input_series:
            findings.append(
                ValidationFinding(
                    ValidationStatus.FAIL,
                    "formula_correctness",
                    f"Derived column {c.key!r} has no recorded input_series — lineage is broken",
                )
            )
            continue
        missing = [k for k in c.input_series if k not in known_keys]
        if missing:
            findings.append(
                ValidationFinding(
                    ValidationStatus.FAIL,
                    "formula_correctness",
                    f"Derived column {c.key!r} references unknown input column(s) {missing}",
                )
            )
        if not c.formula:
            findings.append(
                ValidationFinding(
                    ValidationStatus.WARNING,
                    "formula_correctness",
                    f"Derived column {c.key!r} has no recorded formula",
                )
            )

    if not findings:
        findings.append(ValidationFinding(ValidationStatus.PASS, "table", "no issues found"))

    return ValidationResult(tuple(findings))

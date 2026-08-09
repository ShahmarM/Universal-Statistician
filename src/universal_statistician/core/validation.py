"""Validation layer (section 16): runs after retrieval/calculation, before
an answer is built, and produces structured PASS/WARNING/FAIL findings — a
FAIL means "do not present this as a supported numerical answer," not just
a log line.

Two entry points, because the two things being checked exist at different
stages: `validate_series()` runs on one freshly retrieved SeriesResult
(duplicate periods, NaN values that should be None, empty results — things
only visible before a table flattens periods into one dict);
`validate_table()` runs on a ComparisonTable, possibly after compose.py
transformations (citations, unit/frequency consistency across columns,
requested-geography/period coverage, and derived-column lineage integrity).

Deliberately conservative about what "consistency" and "gap" checks claim:
a base column with unknown unit/frequency (SeriesResult.unit is commonly
None today — see core/models.py's docstring) is never treated as
"inconsistent" with another unknown one, only flagged when two columns'
values are *both known* and *differ* — the same "unknown is not a
contradiction" principle already applied in core/selection.py's geographic
scoring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from universal_statistician.core.compose import ComparisonTable
from universal_statistician.core.models import SeriesResult


class ValidationStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


_SEVERITY = {ValidationStatus.PASS: 0, ValidationStatus.WARNING: 1, ValidationStatus.FAIL: 2}


@dataclass(frozen=True)
class ValidationFinding:
    status: ValidationStatus
    #: Short, stable name of the check that produced this finding, e.g.
    #: "citations_exist" — matches section 16's checklist item names where
    #: there's a direct one, so a caller can filter/group by check.
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
        """False means FAIL: section 16's "a FAIL should prevent an
        unsupported numerical answer" — callers building an answer must
        check this before presenting derived numbers."""
        return self.status != ValidationStatus.FAIL

    def as_dict(self) -> dict:
        return {"status": self.status.value, "findings": [f.as_dict() for f in self.findings]}


def _is_nan(value: float | None) -> bool:
    return value is not None and isinstance(value, float) and math.isnan(value)


def validate_series(series: SeriesResult) -> ValidationResult:
    """Checks only visible before observations flatten into a
    ComparisonTable: duplicate periods (a source returning the same period
    twice, which a table's period->value dict would silently collapse),
    NaN values that should have been normalized to None, and an empty
    result."""
    findings: list[ValidationFinding] = []
    label = f"{series.indicator_id}/{series.ref_area}"

    periods = [o.period for o in series.observations]
    duplicate_periods = sorted({p for p in periods if periods.count(p) > 1})
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

    # citations exist for every base series (section 16 + this project's
    # core principle: no number without a source).
    for c in base_columns:
        if c.attribution is None:
            findings.append(
                ValidationFinding(
                    ValidationStatus.FAIL,
                    "citations_exist",
                    f"Base column {c.key!r} has no attribution — cannot cite an official source for it",
                )
            )

    # unit / frequency consistency — only when *both* values are known and differ;
    # unknown is never treated as a contradiction (see module docstring).
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

    # requested geographies actually returned
    for geo in requested_geographies:
        col = next((c for c in base_columns if c.key == geo), None)
        if col is None:
            findings.append(
                ValidationFinding(
                    ValidationStatus.WARNING,
                    "requested_geographies_returned",
                    f"Requested geography {geo!r} has no corresponding column in the result",
                )
            )
        elif not any(table.value_at(p, geo) is not None for p in table.periods()):
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

    # unexpected gaps — conservative: only for columns whose *every* period
    # parses as a plain 4-digit year (same guard with_cagr uses), so this
    # never misfires on monthly/quarterly data it can't reason about safely.
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

    # derived-column lineage integrity ("formula correctness" / "derived
    # values mathematically reproduce from inputs", read structurally: every
    # derived column must record what it was computed from, and every
    # referenced input must actually exist in this table).
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

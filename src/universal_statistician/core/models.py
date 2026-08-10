"""Normalized data types shared by every provider and interface.

Every value that leaves a Provider carries an Attribution: which official
source it came from, under what dataset id, and when it was retrieved. This
is not optional metadata — it is the mechanism that lets the assistant claim
"only official sources" for anything it reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Attribution:
    source_id: str
    source_name: str
    dataset_id: str
    retrieved_at: datetime
    source_url: str | None = None

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "dataset_id": self.dataset_id,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_url": self.source_url,
        }

    @staticmethod
    def from_dict(payload: dict) -> "Attribution":
        return Attribution(
            source_id=payload["source_id"],
            source_name=payload["source_name"],
            dataset_id=payload["dataset_id"],
            retrieved_at=datetime.fromisoformat(payload["retrieved_at"]),
            source_url=payload["source_url"],
        )


@dataclass(frozen=True)
class StatisticalSemantics:
    """Structured statistical semantics for an indicator/series. Every
    field defaults to None ("unknown"), populated only from a source's own
    structurally-known metadata — never inferred from a display name.
    validation.py flags combining series with known-incompatible semantics.
    """

    #: "nominal", "real", "index", "percent", "percentage_points".
    price_basis: str | None = None
    #: ISO 4217-style code (e.g. "EUR"), when fixed by the source.
    currency: str | None = None
    #: e.g. "millions" — the scale a currency value is expressed in.
    currency_scale: str | None = None
    #: True/False only when the source states it; None (not False) when
    #: unknown.
    per_capita: bool | None = None
    seasonally_adjusted: bool | None = None
    #: Reference year of a "real"/"index" series (e.g. "2015").
    base_year: str | None = None
    #: Verbatim methodology note from the source; never a fabricated
    #: summary.
    methodology_notes: str | None = None

    def as_dict(self) -> dict:
        return {
            "price_basis": self.price_basis,
            "currency": self.currency,
            "currency_scale": self.currency_scale,
            "per_capita": self.per_capita,
            "seasonally_adjusted": self.seasonally_adjusted,
            "base_year": self.base_year,
            "methodology_notes": self.methodology_notes,
        }

    @staticmethod
    def from_dict(payload: dict) -> "StatisticalSemantics":
        return StatisticalSemantics(
            price_basis=payload.get("price_basis"),
            currency=payload.get("currency"),
            currency_scale=payload.get("currency_scale"),
            per_capita=payload.get("per_capita"),
            seasonally_adjusted=payload.get("seasonally_adjusted"),
            base_year=payload.get("base_year"),
            methodology_notes=payload.get("methodology_notes"),
        )


@dataclass(frozen=True)
class Observation:
    period: str
    value: float | None
    #: "actual"/"provisional"/"forecast"/"estimate" when the provider's
    #: protocol exposes it (e.g. SDMX OBS_STATUS); None means unknown,
    #: never assumed "actual".
    status: str | None = None


@dataclass(frozen=True)
class SeriesResult:
    indicator_id: str
    ref_area: str
    frequency: str
    observations: tuple[Observation, ...]
    attribution: Attribution
    #: Unit of measure, when the provider's protocol exposes it (commonly
    #: None today — most sources don't return it inline).
    unit: str | None = None
    #: Populated only when a dataflow's fixed dimensions make it certain;
    #: never inferred from the indicator's name.
    semantics: "StatisticalSemantics | None" = None

    def as_dict(self) -> dict:
        return {
            "indicator_id": self.indicator_id,
            "ref_area": self.ref_area,
            "frequency": self.frequency,
            "observations": [
                {"period": o.period, "value": o.value, "status": o.status} for o in self.observations
            ],
            "attribution": self.attribution.as_dict(),
            "unit": self.unit,
            "semantics": self.semantics.as_dict() if self.semantics is not None else None,
        }

    @staticmethod
    def from_dict(payload: dict) -> "SeriesResult":
        """Inverse of as_dict(). `status` must round-trip too, or the TTL
        cache would silently drop it on every hit."""
        semantics_payload = payload.get("semantics")
        return SeriesResult(
            indicator_id=payload["indicator_id"],
            ref_area=payload["ref_area"],
            frequency=payload["frequency"],
            observations=tuple(
                Observation(period=o["period"], value=o["value"], status=o.get("status"))
                for o in payload["observations"]
            ),
            attribution=Attribution.from_dict(payload["attribution"]),
            unit=payload.get("unit"),
            semantics=StatisticalSemantics.from_dict(semantics_payload)
            if semantics_payload is not None
            else None,
        )


@dataclass(frozen=True)
class DimensionValue:
    code: str
    label: str | None = None

    def as_dict(self) -> dict:
        return {"code": self.code, "label": self.label}

    @staticmethod
    def from_dict(payload: dict) -> "DimensionValue":
        return DimensionValue(code=payload["code"], label=payload.get("label"))


@dataclass(frozen=True)
class DimensionSpec:
    """One dataset dimension (e.g. "unit", "s_adj") beyond the first-class
    indicator/ref_area pair, with its published values where known."""

    code: str
    label: str | None = None
    values: tuple[DimensionValue, ...] = ()

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "values": [v.as_dict() for v in self.values],
        }

    @staticmethod
    def from_dict(payload: dict) -> "DimensionSpec":
        return DimensionSpec(
            code=payload["code"],
            label=payload.get("label"),
            values=tuple(DimensionValue.from_dict(v) for v in payload.get("values", [])),
        )


@dataclass(frozen=True)
class IndicatorMeta:
    """A searchable catalog entry. indicator_id/source_id identify the
    queryable series; dataset_id the dataflow it belongs to; everything
    else is optional metadata a source may publish."""

    indicator_id: str
    name: str
    source_id: str
    description: str | None = None
    dataset_id: str | None = None
    unit: str | None = None
    frequency: str | None = None
    geographic_coverage: tuple[str, ...] | None = None
    dimensions: tuple[DimensionSpec, ...] | None = None
    source_organization: str | None = None
    official_url: str | None = None
    #: When the *source* last updated this indicator (not our ingestion
    #: time).
    last_updated: str | None = None
    keywords: tuple[str, ...] | None = None
    semantics: "StatisticalSemantics | None" = None

    def as_dict(self) -> dict:
        return {
            "indicator_id": self.indicator_id,
            "name": self.name,
            "source_id": self.source_id,
            "description": self.description,
            "dataset_id": self.dataset_id,
            "unit": self.unit,
            "frequency": self.frequency,
            "geographic_coverage": (
                list(self.geographic_coverage) if self.geographic_coverage is not None else None
            ),
            "dimensions": (
                [d.as_dict() for d in self.dimensions] if self.dimensions is not None else None
            ),
            "source_organization": self.source_organization,
            "official_url": self.official_url,
            "last_updated": self.last_updated,
            "keywords": list(self.keywords) if self.keywords is not None else None,
            "semantics": self.semantics.as_dict() if self.semantics is not None else None,
        }

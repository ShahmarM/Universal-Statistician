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
class Observation:
    period: str
    value: float | None


@dataclass(frozen=True)
class SeriesResult:
    indicator_id: str
    ref_area: str
    frequency: str
    observations: tuple[Observation, ...]
    attribution: Attribution

    def as_dict(self) -> dict:
        return {
            "indicator_id": self.indicator_id,
            "ref_area": self.ref_area,
            "frequency": self.frequency,
            "observations": [
                {"period": o.period, "value": o.value} for o in self.observations
            ],
            "attribution": self.attribution.as_dict(),
        }

    @staticmethod
    def from_dict(payload: dict) -> "SeriesResult":
        """Inverse of as_dict() — round-trips a result through the cache
        without losing its type (callers should never see a bare dict)."""
        return SeriesResult(
            indicator_id=payload["indicator_id"],
            ref_area=payload["ref_area"],
            frequency=payload["frequency"],
            observations=tuple(
                Observation(period=o["period"], value=o["value"])
                for o in payload["observations"]
            ),
            attribution=Attribution.from_dict(payload["attribution"]),
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
    """One dimension of a dataset/series (e.g. "geo", "unit", "na_item"),
    with the values a source has published for it where known.

    Deliberately separate from `IndicatorMeta.indicator_id`/`ref_area`: those
    two are the two dimensions every existing Provider already treats as
    first-class (the {indicator}/{ref_area} placeholders in
    providers/registry.py). `DimensionSpec` is for the *other* dimensions a
    multidimensional dataset (Eurostat, IMF, OECD, ...) may pin or expose —
    e.g. Eurostat's `unit`, `s_adj` — captured for search/filtering/query
    planning without forcing every provider to model them yet.
    """

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
    """A searchable catalog entry.

    Distinguishes what a source actually publishes from what we know about
    it: `indicator_id`/`source_id` identify the queryable series (the pair
    every `QueryEngine.get_series()` call needs), `dataset_id` identifies the
    dataset/dataflow it comes from (may cover many indicators — see
    providers/registry.py's module docstring on why a registry entry is
    "source + dataflow", not "source" alone), and everything else is
    descriptive metadata a source *may* publish, kept optional so a source
    that can only offer a bare code + label (e.g. a not-yet-fully-discovered
    provider) still produces a valid, searchable entry — richer sources fill
    in more without needing a different model.
    """

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
    #: ISO date/datetime string for when the *source* last updated this
    #: indicator's metadata (not when we last ingested it — see
    #: core/ingestion.py's IngestionReport for our own ingestion timestamps).
    last_updated: str | None = None
    keywords: tuple[str, ...] | None = None

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
        }

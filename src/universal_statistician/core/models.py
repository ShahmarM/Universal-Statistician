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
class IndicatorMeta:
    indicator_id: str
    name: str
    source_id: str
    description: str | None = None

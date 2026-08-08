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
            "attribution": {
                "source_id": self.attribution.source_id,
                "source_name": self.attribution.source_name,
                "dataset_id": self.attribution.dataset_id,
                "retrieved_at": self.attribution.retrieved_at.isoformat(),
                "source_url": self.attribution.source_url,
            },
        }

    @staticmethod
    def from_dict(payload: dict) -> "SeriesResult":
        """Inverse of as_dict() — round-trips a result through the cache
        without losing its type (callers should never see a bare dict)."""
        attribution = payload["attribution"]
        return SeriesResult(
            indicator_id=payload["indicator_id"],
            ref_area=payload["ref_area"],
            frequency=payload["frequency"],
            observations=tuple(
                Observation(period=o["period"], value=o["value"])
                for o in payload["observations"]
            ),
            attribution=Attribution(
                source_id=attribution["source_id"],
                source_name=attribution["source_name"],
                dataset_id=attribution["dataset_id"],
                retrieved_at=datetime.fromisoformat(attribution["retrieved_at"]),
                source_url=attribution["source_url"],
            ),
        )


@dataclass(frozen=True)
class IndicatorMeta:
    indicator_id: str
    name: str
    source_id: str
    description: str | None = None

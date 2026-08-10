"""The Provider interface: one implementation per queryable dataset.

Interfaces talk to sources only through this contract, never a source's
raw client library. Indicator search is deliberately absent: discovery is
cross-source by nature and belongs to the QueryEngine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.core.models import SeriesResult


class Provider(ABC):
    source_id: str
    source_name: str
    #: Seconds a get_series() result for this provider may be cached by
    #: QueryEngine before being re-fetched.
    cache_ttl_seconds: int

    @abstractmethod
    def get_series(
        self,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        """Fetch one indicator's time series for one area, normalized and attributed."""

    @abstractmethod
    def describe(self) -> dict:
        """Machine-readable description of this source (id, name, url)."""


@runtime_checkable
class MetadataDiscoverable(Protocol):
    """Optional capability: a Provider that can discover its own catalog
    entries from the live source. Not part of the required contract —
    discovery needs a metadata/codelist API not every source has, and a
    provider without it keeps relying on the manual catalog seed. A
    Protocol, not an ABC mixin, so a subclass gains it by adding one method
    without changing its base class."""

    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        """Every indicator the source currently publishes, normalized for
        Catalog.add(), populating as much optional metadata as the source's
        discovery API exposes."""
        ...

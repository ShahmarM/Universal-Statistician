"""The Provider interface: one implementation per queryable dataset.

Any interface (MCP server, CLI, future API) talks to sources only through this
contract, never through a source's raw client library. That's what lets the
same query engine grow from one source to a dozen without the interfaces
having to know anything changed.

Indicator search is deliberately *not* part of this interface: discovery is
cross-source by nature (see core/catalog.py) and belongs to the QueryEngine,
not to any one provider.
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
    entries from the live source, for core/ingestion.py to call.

    Deliberately *not* part of the required Provider contract: discovery
    needs a source's metadata/codelist API, which not every provider has
    (yet) implemented, or reachable network access to call at all (see
    providers/registry.py's module docstring on why OECD isn't registered —
    the same "no verified example, don't guess" principle applies here). A
    provider that doesn't implement this simply keeps relying on a manually
    curated catalog seed (providers/catalog_seed.py), same as every provider
    today — ingestion is additive, not a requirement to keep working.

    A `Protocol` rather than an ABC mixin so an existing Provider subclass
    can gain this capability by adding one method, without changing its base
    class or its `isinstance` identity for anything already checking
    `isinstance(x, Provider)`.
    """

    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        """Return every indicator this provider's source currently publishes,
        normalized as IndicatorEntry objects ready for Catalog.add().

        Implementations should populate as much of IndicatorEntry's optional
        metadata (unit, frequency, dimensions, geographic_coverage, ...) as
        the source's own discovery API exposes — see core/models.py's
        IndicatorMeta docstring on why every one of those fields is optional
        rather than required."""
        ...

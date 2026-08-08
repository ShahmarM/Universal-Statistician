"""Core query engine: the single place every interface (MCP, CLI, ...) calls into.

Interfaces never talk to a Provider directly — they go through the engine, so
adding a new interface never means re-implementing source lookup.
"""

from __future__ import annotations

from universal_statistician.core.models import SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider


class UnknownSourceError(KeyError):
    pass


class QueryEngine:
    def __init__(self, providers: dict[str, Provider]) -> None:
        self._providers = providers

    def list_sources(self) -> list[dict]:
        return [p.describe() for p in self._providers.values()]

    def _get_provider(self, source_id: str) -> Provider:
        try:
            return self._providers[source_id]
        except KeyError:
            raise UnknownSourceError(
                f"Unknown source {source_id!r}; known sources: {sorted(self._providers)}"
            ) from None

    def get_series(
        self,
        source_id: str,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        provider = self._get_provider(source_id)
        return provider.get_series(
            indicator_id, ref_area, start_period=start_period, end_period=end_period
        )


def default_engine() -> QueryEngine:
    """QueryEngine wired up with every SDMX source in the registry."""
    providers: dict[str, Provider] = {
        source_id: SDMXProvider(config) for source_id, config in SOURCES.items()
    }
    return QueryEngine(providers)

"""Core query engine: the single place every interface (MCP, CLI, ...) calls into.

Interfaces never talk to a Provider, Catalog, or Cache directly — they go
through the engine, so adding a new interface never means re-implementing
source lookup, indicator search, or caching.
"""

from __future__ import annotations

from universal_statistician.core.cache import Cache
from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import IngestionReport, ingest_source, refresh_all
from universal_statistician.core.models import IndicatorMeta, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.catalog_seed import CATALOG_SEED
from universal_statistician.providers.eurostat_provider import EurostatProvider
from universal_statistician.providers.imf_provider import IMFProvider
from universal_statistician.providers.pxweb_provider import PXWebProvider
from universal_statistician.providers.pxweb_registry import PXWEB_SOURCES
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider
from universal_statistician.providers.worldbank_provider import WorldBankProvider

_DISCOVERABLE_SDMX_PROVIDERS = {
    "WB_WDI": WorldBankProvider,
    "IMF_DATA_CPI": IMFProvider,
    "ESTAT_NAMA_10_GDP": EurostatProvider,
}


class UnknownSourceError(KeyError):
    pass


class QueryEngine:
    def __init__(
        self,
        providers: dict[str, Provider],
        catalog: Catalog | None = None,
        cache: Cache | None = None,
    ) -> None:
        self._providers = providers
        self._catalog = catalog or Catalog()
        self._cache = cache or Cache()

    def list_sources(self) -> list[dict]:
        return [p.describe() for p in self._providers.values()]

    def describe_source(self, source_id: str) -> dict:
        return self._get_provider(source_id).describe()

    def search_indicator(self, query: str, limit: int = 20) -> list[IndicatorMeta]:
        return self._catalog.search(query, limit=limit)

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
        cache_key = Cache.make_key(source_id, indicator_id, ref_area, start_period, end_period)

        cached = self._cache.get(cache_key)
        if cached is not None:
            return SeriesResult.from_dict(cached)

        result = provider.get_series(
            indicator_id, ref_area, start_period=start_period, end_period=end_period
        )
        self._cache.set(cache_key, result.as_dict(), ttl_seconds=provider.cache_ttl_seconds)
        return result

    def refresh_catalog(self, source_id: str | None = None) -> list[IngestionReport]:
        """Re-discover and upsert catalog metadata (core/ingestion.py) for one
        source, or every discoverable source when source_id is omitted.

        Deliberately not called automatically anywhere (not at startup, not
        from get_series/search_indicator) — ingestion is an explicit,
        admin-triggered action (see `ustat catalog refresh`), so an ordinary
        query never pays for a metadata discovery call it didn't ask for.
        """
        if source_id is not None:
            return [ingest_source(source_id, self._get_provider(source_id), self._catalog)]
        return refresh_all(self._providers, self._catalog)

    def catalog_stats(self) -> dict[str, int]:
        """Indicator count per source currently in the catalog (see
        Catalog.stats()) — a cheap way to see ingestion's effect without
        re-running it."""
        return self._catalog.stats()


def default_engine() -> QueryEngine:
    """QueryEngine wired up with every registered source (SDMX and PX-Web
    alike), and a catalog pre-populated from providers/catalog_seed.py.

    Provider construction must stay network-free here: this runs at startup
    for every interface (MCP, CLI, API), before anyone has asked for
    anything from a specific source, so a source that's unreachable at that
    moment must not break every other source's availability. SDMXProvider
    and PXWebProvider both connect lazily on first use for exactly this
    reason (see PXWebProvider's docstring for the bug this would otherwise
    cause).
    """
    providers: dict[str, Provider] = {
        # Some SDMX sources additionally support catalog discovery (Phases
        # 2-3) via a dedicated subclass — see WorldBankProvider's and
        # IMFProvider's docstrings for why each is a subclass rather than a
        # change to SDMXProvider itself (their discovery mechanisms are
        # genuinely different from each other, and from sources that don't
        # have one yet).
        source_id: _DISCOVERABLE_SDMX_PROVIDERS.get(source_id, SDMXProvider)(config)
        for source_id, config in SOURCES.items()
    }
    providers.update(
        {source_id: PXWebProvider(config) for source_id, config in PXWEB_SOURCES.items()}
    )
    catalog = Catalog()
    catalog.add(CATALOG_SEED)
    return QueryEngine(providers, catalog)

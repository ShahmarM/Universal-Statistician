"""Core query engine: the single place every interface (MCP, CLI, ...) calls into.

Interfaces never talk to a Provider, Catalog, or Cache directly — they go
through the engine, so adding a new interface never means re-implementing
source lookup, indicator search, or caching.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from universal_statistician.core.cache import Cache
from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import IngestionReport, ingest_source, refresh_all
from universal_statistician.core.models import IndicatorMeta, SeriesResult
from universal_statistician.providers.base import Provider
from universal_statistician.providers.catalog_seed import CATALOG_SEED
from universal_statistician.providers.census_provider import CensusProvider
from universal_statistician.providers.census_registry import SOURCES as CENSUS_SOURCES
from universal_statistician.providers.eurostat_provider import EurostatProvider
from universal_statistician.providers.imf_provider import IMFProvider
from universal_statistician.providers.oecd_provider import OECDProvider
from universal_statistician.providers.pxweb_provider import PXWebProvider
from universal_statistician.providers.pxweb_registry import PXWEB_SOURCES
from universal_statistician.providers.registry import SOURCES
from universal_statistician.providers.sdmx_provider import SDMXProvider
from universal_statistician.providers.worldbank_provider import WorldBankProvider

_DISCOVERABLE_SDMX_PROVIDERS = {
    "WB_WDI": WorldBankProvider,
    "IMF_DATA_CPI": IMFProvider,
    "ESTAT_NAMA_10_GDP": EurostatProvider,
    "OECD_NAMAIN10": OECDProvider,
}

#: Logs only public identifiers and timings, never secrets.
logger = logging.getLogger(__name__)


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
        results = self._catalog.search(query, limit=limit)
        logger.info(
            "catalog_search", extra={"query": query, "limit": limit, "result_count": len(results)}
        )
        return results

    def describe_indicator(self, source_id: str, indicator_id: str) -> IndicatorMeta | None:
        """Direct-by-id catalog lookup — no ranking/text matching."""
        return self._catalog.get(source_id, indicator_id)

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
        log_context = {
            "source_id": source_id,
            "indicator_id": indicator_id,
            "ref_area": ref_area,
            "start_period": start_period,
            "end_period": end_period,
        }

        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("cache_hit", extra=log_context)
            return SeriesResult.from_dict(cached)

        logger.info("cache_miss", extra=log_context)
        started_at = time.monotonic()
        result = provider.get_series(
            indicator_id, ref_area, start_period=start_period, end_period=end_period
        )
        elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
        logger.info("provider_request_completed", extra={**log_context, "elapsed_ms": elapsed_ms})

        self._cache.set(cache_key, result.as_dict(), ttl_seconds=provider.cache_ttl_seconds)
        return result

    def refresh_catalog(self, source_id: str | None = None) -> list[IngestionReport]:
        """Re-discover and upsert catalog metadata for one source, or all.
        Only ever explicit/admin-triggered — never run automatically, so an
        ordinary query never pays for discovery."""
        if source_id is not None:
            return [ingest_source(source_id, self._get_provider(source_id), self._catalog)]
        return refresh_all(self._providers, self._catalog)

    def catalog_stats(self) -> dict:
        """Catalog-health snapshot (see Catalog.summary())."""
        return self._catalog.summary()


#: Default on-disk catalog path. Overridable via USTAT_CATALOG_DB_PATH;
#: the literal ":memory:" opts into a non-persistent catalog (what tests
#: use, set in tests/conftest.py before any interface module is imported).
DEFAULT_CATALOG_DB_PATH = Path.home() / ".universal_statistician" / "catalog.db"


def _catalog_db_path() -> str:
    return os.environ.get("USTAT_CATALOG_DB_PATH", str(DEFAULT_CATALOG_DB_PATH))


def _open_catalog() -> Catalog:
    path = _catalog_db_path()
    if path == ":memory:":
        catalog = Catalog()
        catalog.add(CATALOG_SEED)
        return catalog

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    catalog = Catalog(connection)
    if not catalog.stats():
        # Seed only an empty database — re-seeding would overwrite richer
        # discovered metadata with the seed's minimal placeholders.
        catalog.add(CATALOG_SEED)
    return catalog


def default_engine() -> QueryEngine:
    """QueryEngine wired up with every registered source and a persistent
    catalog. Provider construction must stay network-free: this runs at
    startup for every interface, and one unreachable source must not break
    the rest (providers connect lazily on first use)."""
    providers: dict[str, Provider] = {
        source_id: _DISCOVERABLE_SDMX_PROVIDERS.get(source_id, SDMXProvider)(config)
        for source_id, config in SOURCES.items()
    }
    providers.update(
        {source_id: PXWebProvider(config) for source_id, config in PXWEB_SOURCES.items()}
    )
    providers.update(
        {source_id: CensusProvider(config) for source_id, config in CENSUS_SOURCES.items()}
    )
    return QueryEngine(providers, _open_catalog())

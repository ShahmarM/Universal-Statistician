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

#: Structured logging (section 26): catalog searches, cache hit/miss,
#: provider requests, retrieval time. Deliberately stdlib `logging`, not a
#: new dependency — a personal/local tool doesn't need a metrics pipeline,
#: only records worth grepping/forwarding if one is added later. Never logs
#: secrets: nothing here touches ANTHROPIC_API_KEY or any credential, only
#: source/indicator/area identifiers and timings, all already public in
#: this project's own catalog/registry.
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
        """Direct-by-id catalog lookup (Catalog.get()) — unlike
        search_indicator(), no ranking/text-matching involved: used by the
        agent's inspect_series tool (agent/tools.py) to fetch full metadata
        for one specific catalog_id a caller already has, e.g. from a prior
        search_series result."""
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

    def catalog_stats(self) -> dict:
        """Catalog-health snapshot (see Catalog.summary()) — sources/
        datasets/indicators counts, per-source breakdown, last refresh
        time — a cheap way to see ingestion's effect without re-running it."""
        return self._catalog.summary()


#: Where the catalog's SQLite database lives by default (Phase B: "the
#: catalog must persist between application restarts... do not rely on an
#: in-memory catalog for the normal deployed application"). Overridable via
#: USTAT_CATALOG_DB_PATH; the literal value ":memory:" opts back into a
#: non-persistent catalog — what every offline test in this project uses
#: (see tests/conftest.py, which sets this env var before cli.py/api.py/
#: mcp_server.py — each of which builds a default_engine() at import time —
#: are ever imported, so the test suite never touches a real file here).
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
        # First run against this database file: seed the small set of
        # indicators already verified end-to-end in get_series() (see
        # providers/catalog_seed.py), so search/get_series work before
        # anyone has run `ustat catalog refresh`. Never re-seeds an
        # already-populated catalog on a later restart — that would
        # silently overwrite richer, discovered metadata (Phase 2-6) with
        # the seed's minimal placeholders every time the app starts.
        catalog.add(CATALOG_SEED)
    return catalog


def default_engine() -> QueryEngine:
    """QueryEngine wired up with every registered source (SDMX, PX-Web, and
    Census alike), and a catalog persisted to disk (see _open_catalog()) so
    metadata discovered via `ustat catalog refresh` survives restarts.

    Provider construction must stay network-free here: this runs at startup
    for every interface (MCP, CLI, API), before anyone has asked for
    anything from a specific source, so a source that's unreachable at that
    moment must not break every other source's availability. SDMXProvider
    and PXWebProvider both connect lazily on first use for exactly this
    reason (see PXWebProvider's docstring for the bug this would otherwise
    cause). Opening the catalog's own SQLite file is a local disk operation,
    not a network call, so it stays safe to do unconditionally here.
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
    providers.update(
        {source_id: CensusProvider(config) for source_id, config in CENSUS_SOURCES.items()}
    )
    return QueryEngine(providers, _open_catalog())

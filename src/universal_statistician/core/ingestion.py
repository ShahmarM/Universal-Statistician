"""Metadata ingestion: official APIs -> discovery -> normalization ->
catalog. Providers opt in via MetadataDiscoverable and return normalized
IndicatorEntry objects, so the catalog stays independent of any wire
protocol. Synchronous and admin-triggered only — nothing downloads a whole
statistical database as a side effect of a user's query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.providers.base import MetadataDiscoverable, Provider


@dataclass(frozen=True)
class IngestionReport:
    """Structured record of what one ingest_source() run changed."""

    source_id: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: tuple[str, ...] = ()
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def _primary_name(entry: IndicatorEntry) -> str | None:
    if not entry.names:
        return None
    return entry.names.get("en") or next(iter(entry.names.values()))


def _entry_matches_existing(entry: IndicatorEntry, existing) -> bool:
    """True when a discovered entry matches what the catalog already has."""
    return (
        _primary_name(entry) == existing.name
        and entry.description == existing.description
        and entry.dataset_id == existing.dataset_id
        and entry.unit == existing.unit
        and entry.frequency == existing.frequency
        and entry.geographic_coverage == existing.geographic_coverage
        and entry.dimensions == existing.dimensions
        and entry.source_organization == existing.source_organization
        and entry.official_url == existing.official_url
        and entry.last_updated == existing.last_updated
        and entry.keywords == existing.keywords
    )


def ingest_source(source_id: str, provider: Provider, catalog: Catalog) -> IngestionReport:
    """Discover and upsert one source's catalog entries. A provider without
    discovery support reports an explanatory error rather than raising, so
    refresh_all() doesn't need to special-case it."""
    started_at = datetime.now(timezone.utc).isoformat()
    if not isinstance(provider, MetadataDiscoverable):
        return IngestionReport(
            source_id=source_id,
            errors=(f"Source {source_id!r} does not support metadata discovery.",),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        entries = provider.discover_catalog_entries()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return IngestionReport(
            source_id=source_id,
            errors=(f"Discovery failed for {source_id!r}: {exc}",),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    # One bulk read per source rather than a query per discovered entry:
    # discovery can return tens of thousands of entries for one source.
    existing_by_source: dict[str, dict] = {}

    added = updated = unchanged = 0
    changed_entries: list[IndicatorEntry] = []
    for entry in entries:
        known = existing_by_source.get(entry.source_id)
        if known is None:
            known = existing_by_source[entry.source_id] = catalog.get_all_for_source(entry.source_id)
        existing = known.get(entry.indicator_id)
        if existing is None:
            added += 1
            changed_entries.append(entry)
        elif _entry_matches_existing(entry, existing):
            unchanged += 1
        else:
            updated += 1
            changed_entries.append(entry)

    # Write only new/changed entries: Catalog.add()'s DELETE+INSERT against
    # the FTS5 table is real work per entry, and a repeat refresh of a
    # large, stable source would otherwise redo all of it for nothing.
    catalog.add(changed_entries)

    return IngestionReport(
        source_id=source_id,
        added=added,
        updated=updated,
        unchanged=unchanged,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def refresh_all(providers: dict[str, Provider], catalog: Catalog) -> list[IngestionReport]:
    """Run ingest_source() for every discovery-capable provider. Others are
    skipped silently: not every source being discoverable is the expected
    steady state, not a failure."""
    return [
        ingest_source(source_id, provider, catalog)
        for source_id, provider in providers.items()
        if isinstance(provider, MetadataDiscoverable)
    ]

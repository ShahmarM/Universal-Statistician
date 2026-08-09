"""Metadata ingestion: official APIs -> discovery -> normalization -> catalog.

This is the pipeline that lets the catalog grow from a handful of manually
seeded indicators (providers/catalog_seed.py) into one populated from what a
source actually publishes, without the catalog depending on any particular
provider's wire protocol — every provider that opts in does so by
implementing `MetadataDiscoverable` (providers/base.py) and returning plain
`IndicatorEntry` objects, the same normalized shape catalog_seed.py already
uses.

Deliberately synchronous and admin-triggered (see `ustat catalog refresh` in
cli.py), not run on every request: observation retrieval stays targeted
(QueryEngine.get_series), and nothing here downloads an entire statistical
database as a side effect of a user's search or get_series call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.providers.base import MetadataDiscoverable, Provider


@dataclass(frozen=True)
class IngestionReport:
    """Result of one ingest_source() run — the structured record of what
    changed, per phase 7's requirement to detect changes and avoid silent
    duplication rather than just "it ran"."""

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
    """True if a freshly discovered entry carries the same metadata the
    catalog already has for that (source_id, indicator_id) — i.e. this
    refresh found nothing new for it."""
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
    """Discover and upsert one source's catalog entries.

    A provider that doesn't implement MetadataDiscoverable produces a report
    with a single explanatory error rather than raising — so a caller looping
    over many sources (refresh_all) doesn't need to special-case providers
    that only support manual seeding, and still gets a structured answer for
    "why nothing happened" instead of a crash or silent no-op.
    """
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

    added = updated = unchanged = 0
    changed_entries: list[IndicatorEntry] = []
    for entry in entries:
        existing = catalog.get(entry.source_id, entry.indicator_id)
        if existing is None:
            added += 1
            changed_entries.append(entry)
        elif _entry_matches_existing(entry, existing):
            unchanged += 1
        else:
            updated += 1
            changed_entries.append(entry)

    # Only write entries that are actually new or changed (Phase H: a real,
    # live-discovered performance issue) — Catalog.add()'s DELETE+INSERT
    # against the `indicators` FTS5 table is real, non-trivial work per
    # entry, and re-running it for entries that provably haven't changed
    # (the common case on a repeat `ustat catalog refresh` of a large,
    # mostly-stable source) was pure waste, turning every idempotent
    # re-refresh into O(n) unnecessary FTS writes.
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
    """Run ingest_source() for every registered provider that supports
    discovery; providers that don't are silently skipped here (not reported
    as errors) since "not every source is discoverable yet" is the expected
    steady state, not a failure — see MetadataDiscoverable's docstring."""
    return [
        ingest_source(source_id, provider, catalog)
        for source_id, provider in providers.items()
        if isinstance(provider, MetadataDiscoverable)
    ]

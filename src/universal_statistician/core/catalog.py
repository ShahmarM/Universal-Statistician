"""Local, multilingual full-text index over indicator metadata.

This is what makes the assistant "universal" rather than just a thin SDMX
client: raw provider access only works if you already know an indicator's
code. The catalog is built once from each registered dataset's seed metadata
(see providers/catalog_seed.py) or from a provider's own discovered metadata
(see core/ingestion.py) and searched locally — instant and offline, no
per-query network round trip.

Two tables back this:

- `indicators`, an FTS5 virtual table, unchanged in spirit from the original
  design: one row per (indicator, language), giving free multilingual
  full-text search. Extended with a few more *searchable* columns (keywords,
  unit, source_organization, geo) per this phase's requirement that search
  cover more than just the name/description.
- `catalog_meta`, a plain table keyed by (source_id, indicator_id), holding
  the richer optional metadata (dataset_id, dimensions, geographic coverage,
  ...) that doesn't need full-text search — just retrieval alongside a
  search hit. Kept separate from the FTS table because FTS5 doesn't support
  a primary key / true UPDATE-in-place, which `catalog_meta` needs for
  ingestion to upsert (see add()) rather than accumulate duplicates on every
  refresh.

Both are plain SQLite tables/indexes — deliberately nothing here depends on
SQLite-only syntax beyond FTS5 itself, so a later move to PostgreSQL (with
its own full-text search) would replace this module's internals without
changing IndicatorEntry/IndicatorMeta or any caller.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from universal_statistician.core.models import DimensionSpec, IndicatorMeta, StatisticalSemantics

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS indicators USING fts5(
    indicator_id UNINDEXED,
    source_id UNINDEXED,
    lang UNINDEXED,
    name,
    description UNINDEXED,
    keywords,
    unit,
    source_organization,
    geo
);

CREATE TABLE IF NOT EXISTS catalog_meta (
    source_id TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    name TEXT,
    description TEXT,
    dataset_id TEXT,
    unit TEXT,
    frequency TEXT,
    geographic_coverage TEXT,
    dimensions TEXT,
    source_organization TEXT,
    official_url TEXT,
    last_updated TEXT,
    keywords TEXT,
    semantics TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (source_id, indicator_id)
);
"""

#: Phase F added `catalog_meta.semantics`, Phase H added `.name`/
#: `.description`, both after Phase B's persistent on-disk catalog already
#: shipped — `CREATE TABLE IF NOT EXISTS` above is a no-op against a real
#: ~/.universal_statistician/catalog.db file created before either phase,
#: so a plain schema-string change alone would silently leave a column
#: missing and crash the first INSERT/SELECT that needs it. Guarded,
#: idempotent migration below (like _SCHEMA itself, safe to run on every
#: Catalog() construction).
_MIGRATIONS: tuple[str, ...] = ("semantics", "name", "description")


@dataclass(frozen=True)
class IndicatorEntry:
    """One indicator as fed into the catalog: a code plus its label(s) and
    (optionally) the richer metadata a source's discovery API can supply.

    `names` maps language code -> label, e.g. {"en": "...", "fr": "..."}, so a
    query can match whichever language the source published — the multilingual
    requirement is satisfied by indexing every label a source gives us, not by
    translating anything ourselves.

    The remaining fields mirror `IndicatorMeta` and stay optional for the same
    reason: a manually seeded entry (providers/catalog_seed.py) may only have
    a code and a label, while a discovered entry (core/ingestion.py, fed by a
    MetadataDiscoverable provider) can populate all of them.
    """

    indicator_id: str
    source_id: str
    names: dict[str, str]
    description: str | None = None
    dataset_id: str | None = None
    unit: str | None = None
    frequency: str | None = None
    geographic_coverage: tuple[str, ...] | None = None
    dimensions: tuple[DimensionSpec, ...] | None = None
    source_organization: str | None = None
    official_url: str | None = None
    last_updated: str | None = None
    keywords: tuple[str, ...] | None = None
    #: Structured semantics (Phase F) — see StatisticalSemantics.
    semantics: StatisticalSemantics | None = None


def _primary_name(entry: IndicatorEntry) -> str | None:
    if not entry.names:
        return None
    return entry.names.get("en") or next(iter(entry.names.values()))


class Catalog:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        # MCP tool calls run each synchronous tool in a worker thread, not the
        # thread that constructed this engine — sqlite3's default
        # check_same_thread guard would reject every one of those calls.
        # check_same_thread=False plus our own lock keeps the single
        # in-memory connection (required: a fresh connection per thread would
        # each see an *empty* separate ":memory:" database) safe to share.
        self._conn = connection or sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Caller must already hold self._lock. Adds any column _SCHEMA
        gained after a real on-disk catalog.db (Phase B) might already have
        been created without it — see _MIGRATIONS' docstring above."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(catalog_meta)")}
        for column in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE catalog_meta ADD COLUMN {column} TEXT")

    def add(self, entries: list[IndicatorEntry]) -> None:
        """Insert or, for an (source_id, indicator_id) pair already present,
        replace it — so re-running ingestion (core/ingestion.py) to refresh a
        source's metadata updates existing entries instead of accumulating
        duplicates or stale language labels next to current ones.

        Batched with executemany() across the *whole* entries list, not one
        round trip per entry — a real, live-discovered performance issue
        (Phase H): a single source's discovery can return tens of thousands
        of entries (US Census's ACS1 alone has 36,632 variable codes), and
        the original per-entry-loop version issuing 2-3 individual
        `execute()` calls per entry took minutes for a catalog that size.
        """
        if not entries:
            return

        # Last one wins for a duplicate (source_id, indicator_id) within
        # the same batch — matches the old per-entry delete-then-insert
        # loop's behavior. Real discovery payloads shouldn't produce
        # duplicates (codelists are keyed dicts), but batching every
        # DELETE before any INSERT (below, for performance) would
        # otherwise leave two FTS rows for one key instead of the later
        # entry replacing the earlier one.
        deduped: dict[tuple[str, str], IndicatorEntry] = {}
        for entry in entries:
            deduped[(entry.source_id, entry.indicator_id)] = entry
        entries = list(deduped.values())

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.executemany(
                "DELETE FROM indicators WHERE source_id = ? AND indicator_id = ?",
                [(entry.source_id, entry.indicator_id) for entry in entries],
            )

            indicator_rows = [
                (
                    entry.indicator_id,
                    entry.source_id,
                    lang,
                    name,
                    entry.description,
                    " ".join(entry.keywords) if entry.keywords else None,
                    entry.unit,
                    entry.source_organization,
                    " ".join(entry.geographic_coverage) if entry.geographic_coverage else None,
                )
                for entry in entries
                for lang, name in entry.names.items()
            ]
            if indicator_rows:
                self._conn.executemany(
                    "INSERT INTO indicators "
                    "(indicator_id, source_id, lang, name, description, keywords, unit, "
                    "source_organization, geo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    indicator_rows,
                )

            meta_rows = [
                (
                    entry.source_id,
                    entry.indicator_id,
                    _primary_name(entry),
                    entry.description,
                    entry.dataset_id,
                    entry.unit,
                    entry.frequency,
                    json.dumps(list(entry.geographic_coverage))
                    if entry.geographic_coverage is not None
                    else None,
                    json.dumps([d.as_dict() for d in entry.dimensions])
                    if entry.dimensions is not None
                    else None,
                    entry.source_organization,
                    entry.official_url,
                    entry.last_updated,
                    json.dumps(list(entry.keywords)) if entry.keywords is not None else None,
                    json.dumps(entry.semantics.as_dict()) if entry.semantics is not None else None,
                    now,
                )
                for entry in entries
            ]
            self._conn.executemany(
                "INSERT OR REPLACE INTO catalog_meta "
                "(source_id, indicator_id, name, description, dataset_id, unit, frequency, "
                "geographic_coverage, dimensions, source_organization, official_url, "
                "last_updated, keywords, semantics, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                meta_rows,
            )
            self._conn.commit()

    def get(self, source_id: str, indicator_id: str) -> IndicatorMeta | None:
        """Direct lookup by exact (source_id, indicator_id) — no full-text
        matching, and (Phase H) no FTS5 table scan either: queries
        `catalog_meta` alone, which has a real PRIMARY KEY index on
        (source_id, indicator_id). The `indicators` FTS5 table's
        `indicator_id`/`source_id` columns are UNINDEXED (required for a
        full-text virtual table), so filtering by them there forces a full
        table scan of every row — fine for one lookup, but a real,
        live-discovered O(n²) problem for `core/ingestion.py`'s
        change-detection loop, which calls this once per discovered entry
        (a single source can return tens of thousands — US Census's ACS1:
        36,632 variables — which made a full `ustat catalog refresh`
        against it take minutes instead of well under a second)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT name, description, dataset_id, unit, frequency, geographic_coverage, "
                "dimensions, source_organization, official_url, last_updated, keywords, semantics "
                "FROM catalog_meta WHERE source_id = ? AND indicator_id = ?",
                (source_id, indicator_id),
            ).fetchone()
        if row is None:
            return None
        name, description, *meta_row = row
        return self._build(indicator_id, source_id, name, description, tuple(meta_row))

    def search(self, query: str, limit: int = 20) -> list[IndicatorMeta]:
        fts_query = self._fts_query(query)
        if fts_query is None:
            return []

        with self._lock:
            cursor = self._conn.execute(
                "SELECT indicator_id, source_id, name, description FROM indicators "
                "WHERE indicators MATCH ? ORDER BY rank",
                (fts_query,),
            )
            rows = cursor.fetchall()

            seen: set[tuple[str, str]] = set()
            results: list[IndicatorMeta] = []
            for indicator_id, source_id, name, description in rows:
                key = (source_id, indicator_id)
                if key in seen:
                    continue
                seen.add(key)
                meta_row = self._fetch_meta(source_id, indicator_id)
                results.append(self._build(indicator_id, source_id, name, description, meta_row))
                if len(results) >= limit:
                    break
        return results

    def stats(self) -> dict[str, int]:
        """Indicator count per source — a cheap sanity check after ingestion
        (see `ustat catalog stats`), not a substitute for IngestionReport."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id, COUNT(DISTINCT indicator_id) FROM indicators "
                "GROUP BY source_id ORDER BY source_id"
            ).fetchall()
        return {source_id: count for source_id, count in rows}

    def summary(self) -> dict:
        """Catalog-health snapshot for `ustat catalog stats` (production
        catalog population workflow, Phase B): how many sources/datasets/
        indicators are actually in the catalog right now, broken down per
        source, plus when each source was last ingested — everything
        `stats()` alone can't show (it only has the per-source indicator
        count, kept as-is for backward compatibility)."""
        with self._lock:
            per_source = dict(
                self._conn.execute(
                    "SELECT source_id, COUNT(DISTINCT indicator_id) FROM indicators "
                    "GROUP BY source_id ORDER BY source_id"
                ).fetchall()
            )
            (dataset_count,) = self._conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT DISTINCT source_id, dataset_id FROM catalog_meta "
                "  WHERE dataset_id IS NOT NULL"
                ")"
            ).fetchone()
            last_refresh_by_source = dict(
                self._conn.execute(
                    "SELECT source_id, MAX(ingested_at) FROM catalog_meta GROUP BY source_id"
                ).fetchall()
            )
            (overall_last_refresh,) = self._conn.execute(
                "SELECT MAX(ingested_at) FROM catalog_meta"
            ).fetchone()
        return {
            "sources": len(per_source),
            "datasets": dataset_count,
            "indicators": sum(per_source.values()),
            "records_per_source": per_source,
            "last_refresh": overall_last_refresh,
            "last_refresh_by_source": last_refresh_by_source,
        }

    def _fetch_meta(self, source_id: str, indicator_id: str) -> tuple | None:
        """Caller must already hold self._lock."""
        return self._conn.execute(
            "SELECT dataset_id, unit, frequency, geographic_coverage, dimensions, "
            "source_organization, official_url, last_updated, keywords, semantics "
            "FROM catalog_meta WHERE source_id = ? AND indicator_id = ?",
            (source_id, indicator_id),
        ).fetchone()

    @staticmethod
    def _build(
        indicator_id: str,
        source_id: str,
        name: str,
        description: str | None,
        meta_row: tuple | None,
    ) -> IndicatorMeta:
        if meta_row is None:
            return IndicatorMeta(
                indicator_id=indicator_id, name=name, source_id=source_id, description=description
            )
        (
            dataset_id,
            unit,
            frequency,
            geographic_coverage_json,
            dimensions_json,
            source_organization,
            official_url,
            last_updated,
            keywords_json,
            semantics_json,
        ) = meta_row
        return IndicatorMeta(
            indicator_id=indicator_id,
            name=name,
            source_id=source_id,
            description=description,
            dataset_id=dataset_id,
            unit=unit,
            frequency=frequency,
            geographic_coverage=(
                tuple(json.loads(geographic_coverage_json))
                if geographic_coverage_json is not None
                else None
            ),
            dimensions=(
                tuple(DimensionSpec.from_dict(d) for d in json.loads(dimensions_json))
                if dimensions_json is not None
                else None
            ),
            source_organization=source_organization,
            official_url=official_url,
            last_updated=last_updated,
            keywords=tuple(json.loads(keywords_json)) if keywords_json is not None else None,
            semantics=(
                StatisticalSemantics.from_dict(json.loads(semantics_json))
                if semantics_json is not None
                else None
            ),
        )

    @staticmethod
    def _fts_query(query: str) -> str | None:
        # Prefix-match every token so partial words ("popul") still hit, and
        # strip characters FTS5's query syntax treats specially.
        tokens = [
            "".join(ch for ch in token if ch.isalnum()) for token in query.strip().split()
        ]
        tokens = [t for t in tokens if t]
        return " ".join(f"{t}*" for t in tokens) if tokens else None

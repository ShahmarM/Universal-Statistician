"""Local, multilingual full-text index over indicator metadata.

Two SQLite tables: `indicators` (FTS5, one row per indicator+language) for
search, and `catalog_meta` (plain, PK on source_id+indicator_id) for the
richer metadata — separate because FTS5 has no primary key, and ingestion
needs upsert semantics.
"""

from __future__ import annotations

import json
import re
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

#: Columns added after on-disk catalogs shipped; CREATE TABLE IF NOT EXISTS
#: won't add them to an existing db, so _migrate() does, idempotently.
_MIGRATIONS: tuple[str, ...] = ("semantics", "name", "description")


@dataclass(frozen=True)
class IndicatorEntry:
    """One indicator as fed into the catalog. `names` maps language code ->
    label (every source-published label is indexed; nothing is translated).
    Remaining fields mirror IndicatorMeta and are optional — seeded entries
    may have only a code and label."""

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


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


#: bm25() weights over the indexed columns in schema order: name, keywords,
#: unit, source_organization, geo. source_organization is ~0 (not dropped:
#: it still MATCHes) because WB's free-text citations contain unrelated
#: concept words that skewed relevance.
_BM25_COLUMN_WEIGHTS: tuple[float, ...] = (10.0, 2.0, 1.0, 0.0, 0.2)

#: Administrative qualifier words ("Population, total") penalized far less
#: than genuinely narrowing ones ("rural", "youth") in _composite_score.
_GENERIC_QUALIFIER_TOKENS = frozenset(
    {
        "total", "overall", "all", "both", "aggregate", "annual", "current",
        "constant", "modeled", "estimate", "estimates", "national", "index",
        "rate", "rates", "level", "average", "standard", "combined", "ilo",
    }
)


def _composite_score(name: str, query_tokens: list[str], bm25_relevance: float) -> float:
    """Re-rank on top of raw bm25, which favors term-repeating
    sub-breakdowns over the flagship indicator for a bare concept query.
    Scores how much of the name is the query (coverage + exact whole-word
    match) and how little else it says (extra_penalty, discounted for
    generic qualifiers); bm25 breaks ties."""
    if not query_tokens:
        return 0.0
    name_tokens = _tokenize(name)
    coverage = sum(1 for t in query_tokens if any(nt.startswith(t) for nt in name_tokens)) / len(
        query_tokens
    )
    exact = sum(1 for t in query_tokens if t in name_tokens) / len(query_tokens)
    matched_name_tokens = {nt for nt in name_tokens if any(nt.startswith(t) for t in query_tokens)}
    extra_penalty = sum(
        0.4 if t in _GENERIC_QUALIFIER_TOKENS else 1.0
        for t in name_tokens
        if t not in matched_name_tokens
    )
    return coverage * 100.0 + exact * 40.0 - extra_penalty * 4.0 + (-bm25_relevance) * 0.3


class Catalog:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        # MCP/HTTP hosts call tools from worker threads; one shared
        # connection (per-thread :memory: dbs would each be empty) guarded
        # by our own lock, with check_same_thread off.
        self._conn = connection or sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add any _MIGRATIONS column missing from an older on-disk db.
        Caller must hold self._lock."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(catalog_meta)")}
        for column in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE catalog_meta ADD COLUMN {column} TEXT")

    def add(self, entries: list[IndicatorEntry]) -> None:
        """Upsert entries so re-ingestion refreshes instead of duplicating.
        Batched with executemany() — a single source can return tens of
        thousands of entries, and per-entry execute() calls took minutes."""
        if not entries:
            return

        # Last one wins for an in-batch duplicate key: batching all DELETEs
        # before the INSERTs would otherwise leave two FTS rows for one key.
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
        """Exact-key lookup via catalog_meta's PRIMARY KEY index only —
        filtering the FTS table by its UNINDEXED id columns is a full scan,
        which made ingestion's per-entry change detection O(n²)."""
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
        """Full-text search re-ranked by _composite_score() to favor the
        flagship indicator over narrow sub-breakdowns."""
        fts_query = self._fts_query(query)
        if fts_query is None:
            return []
        query_tokens = _tokenize(query)

        with self._lock:
            # Wide bm25-ordered pool (cheap, in C); Python re-ranks only the
            # pool, not every match of a broad prefix query.
            pool_size = max(limit * 20, 200)
            cursor = self._conn.execute(
                "SELECT indicator_id, source_id, name, description, "
                f"bm25(indicators, {', '.join('?' * len(_BM25_COLUMN_WEIGHTS))}) AS relevance "
                "FROM indicators WHERE indicators MATCH ? ORDER BY relevance LIMIT ?",
                (*_BM25_COLUMN_WEIGHTS, fts_query, pool_size),
            )
            rows = cursor.fetchall()

            seen: set[tuple[str, str]] = set()
            scored: list[tuple[float, str, str, str, str | None]] = []
            for indicator_id, source_id, name, description, relevance in rows:
                key = (source_id, indicator_id)
                if key in seen:
                    continue
                seen.add(key)
                composite = _composite_score(name, query_tokens, relevance)
                scored.append((composite, indicator_id, source_id, name, description))
            scored.sort(key=lambda item: item[0], reverse=True)

            results: list[IndicatorMeta] = []
            for _composite, indicator_id, source_id, name, description in scored[:limit]:
                meta_row = self._fetch_meta(source_id, indicator_id)
                results.append(self._build(indicator_id, source_id, name, description, meta_row))
        return results

    def get_all_for_source(self, source_id: str) -> dict[str, IndicatorMeta]:
        """Every indicator this source has, keyed by indicator_id — one
        query instead of one per indicator, for ingestion's change
        detection over sources with tens of thousands of entries."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT indicator_id, name, description, dataset_id, unit, frequency, "
                "geographic_coverage, dimensions, source_organization, official_url, "
                "last_updated, keywords, semantics FROM catalog_meta WHERE source_id = ?",
                (source_id,),
            ).fetchall()
        return {
            row[0]: self._build(row[0], source_id, row[1], row[2], tuple(row[3:])) for row in rows
        }

    def stats(self) -> dict[str, int]:
        """Indicator count per source."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id, COUNT(DISTINCT indicator_id) FROM indicators "
                "GROUP BY source_id ORDER BY source_id"
            ).fetchall()
        return {source_id: count for source_id, count in rows}

    def summary(self) -> dict:
        """Catalog-health snapshot: source/dataset/indicator counts and
        last-ingestion times."""
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
        # Prefix-match every token so partial words ("popul") still hit.
        tokens = _tokenize(query)
        return " ".join(f"{t}*" for t in tokens) if tokens else None

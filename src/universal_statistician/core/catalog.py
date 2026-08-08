"""Local, multilingual full-text index over indicator metadata.

This is what makes the assistant "universal" rather than just a thin SDMX
client: raw provider access only works if you already know an indicator's
code. The catalog is built once from each registered dataset's seed metadata
(see providers/catalog_seed.py) and searched locally — instant and offline,
no per-query network round trip.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from universal_statistician.core.models import IndicatorMeta

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS indicators USING fts5(
    indicator_id UNINDEXED,
    source_id UNINDEXED,
    lang UNINDEXED,
    name,
    description UNINDEXED
)
"""


@dataclass(frozen=True)
class IndicatorEntry:
    """One indicator as fed into the catalog: a code plus its label(s).

    `names` maps language code -> label, e.g. {"en": "...", "fr": "..."}, so a
    query can match whichever language the source published — the multilingual
    requirement is satisfied by indexing every label a source gives us, not by
    translating anything ourselves.
    """

    indicator_id: str
    source_id: str
    names: dict[str, str]
    description: str | None = None


class Catalog:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self._conn = connection or sqlite3.connect(":memory:")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def add(self, entries: list[IndicatorEntry]) -> None:
        rows = [
            (entry.indicator_id, entry.source_id, lang, name, entry.description)
            for entry in entries
            for lang, name in entry.names.items()
        ]
        self._conn.executemany(
            "INSERT INTO indicators (indicator_id, source_id, lang, name, description) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def search(self, query: str, limit: int = 20) -> list[IndicatorMeta]:
        fts_query = self._fts_query(query)
        if fts_query is None:
            return []

        cursor = self._conn.execute(
            "SELECT indicator_id, source_id, name, description FROM indicators "
            "WHERE indicators MATCH ? ORDER BY rank",
            (fts_query,),
        )
        seen: set[tuple[str, str]] = set()
        results: list[IndicatorMeta] = []
        for indicator_id, source_id, name, description in cursor:
            key = (source_id, indicator_id)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                IndicatorMeta(
                    indicator_id=indicator_id,
                    name=name,
                    source_id=source_id,
                    description=description,
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _fts_query(query: str) -> str | None:
        # Prefix-match every token so partial words ("popul") still hit, and
        # strip characters FTS5's query syntax treats specially.
        tokens = [
            "".join(ch for ch in token if ch.isalnum()) for token in query.strip().split()
        ]
        tokens = [t for t in tokens if t]
        return " ".join(f"{t}*" for t in tokens) if tokens else None

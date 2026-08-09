from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from universal_statistician.core.catalog import _SCHEMA, Catalog, IndicatorEntry
from universal_statistician.core.models import DimensionSpec, DimensionValue, StatisticalSemantics
from universal_statistician.providers.catalog_seed import CATALOG_SEED


def build_catalog(entries):
    catalog = Catalog()
    catalog.add(entries)
    return catalog


def test_search_works_from_a_different_thread():
    # Regression test: an MCP server runs each synchronous tool call in a
    # worker thread, not the thread that constructed the engine (and its
    # Catalog). sqlite3's default same-thread guard rejects that outright,
    # and a fresh connection per thread would each see an empty separate
    # ":memory:" database — this caught a real bug end to end via
    # server.call_tool(), not just by calling Catalog directly.
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        results = pool.submit(catalog.search, "population").result()
    assert [r.indicator_id for r in results] == ["SP_POP_TOTL"]


def test_search_matches_english_label():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    results = catalog.search("population")
    assert [r.indicator_id for r in results] == ["SP_POP_TOTL"]
    assert results[0].source_id == "WB_WDI"


def test_search_matches_prefix():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    assert catalog.search("popul")


def test_search_is_multilingual():
    catalog = build_catalog(
        [
            IndicatorEntry(
                indicator_id="B1GQ",
                source_id="ESTAT_NAMA_10_GDP",
                names={
                    "en": "Gross domestic product at market prices",
                    "fr": "Produit intérieur brut aux prix du marché",
                },
            )
        ]
    )
    assert [r.indicator_id for r in catalog.search("gross")] == ["B1GQ"]
    assert [r.indicator_id for r in catalog.search("produit")] == ["B1GQ"]


def test_search_deduplicates_across_languages():
    catalog = build_catalog(
        [
            IndicatorEntry(
                indicator_id="B1GQ",
                source_id="ESTAT_NAMA_10_GDP",
                names={"en": "Gross domestic product", "fr": "Produit intérieur brut"},
            )
        ]
    )
    # A query matching both language rows must still return one result.
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="B1GQ",
                source_id="ESTAT_NAMA_10_GDP",
                names={"de": "Bruttoinlandsprodukt"},
            )
        ]
    )
    results = catalog.search("brut*")
    assert len(results) == 1


def test_search_respects_limit():
    entries = [
        IndicatorEntry(indicator_id=f"IND_{i}", source_id="WB_WDI", names={"en": f"Indicator number {i}"})
        for i in range(5)
    ]
    catalog = build_catalog(entries)
    assert len(catalog.search("indicator", limit=2)) == 2


def test_search_no_match_returns_empty():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    assert catalog.search("nonexistentword") == []


def test_search_blank_query_returns_empty():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    assert catalog.search("   ") == []


def test_catalog_seed_is_searchable_and_matches_registry():
    from universal_statistician.providers.pxweb_registry import PXWEB_SOURCES
    from universal_statistician.providers.registry import SOURCES

    catalog = Catalog()
    catalog.add(CATALOG_SEED)

    known_source_ids = set(SOURCES) | set(PXWEB_SOURCES)
    for entry in CATALOG_SEED:
        # Every seeded indicator must belong to a real registry entry —
        # otherwise get_series() would reject it even though search() found it.
        assert entry.source_id in known_source_ids

    results = catalog.search("population")
    assert any(r.indicator_id == "SP_POP_TOTL" for r in results)


def test_search_enriches_results_with_rich_metadata_when_present():
    entry = IndicatorEntry(
        indicator_id="NY_GDP_PCAP_CD",
        source_id="WB_WDI",
        names={"en": "GDP per capita (current US$)"},
        description="GDP per capita.",
        dataset_id="WDI",
        unit="current US$",
        frequency="A",
        geographic_coverage=("AFG", "USA"),
        dimensions=(DimensionSpec(code="unit", label="Unit", values=(DimensionValue("USD"),)),),
        source_organization="World Bank",
        official_url="https://api.worldbank.org",
        last_updated="2026-01-01",
        keywords=("gdp", "per capita"),
        semantics=StatisticalSemantics(price_basis="nominal", currency="USD"),
    )
    catalog = build_catalog([entry])

    results = catalog.search("gdp per capita")
    assert len(results) == 1
    meta = results[0]
    assert meta.dataset_id == "WDI"
    assert meta.unit == "current US$"
    assert meta.frequency == "A"
    assert meta.geographic_coverage == ("AFG", "USA")
    assert meta.dimensions == (
        DimensionSpec(code="unit", label="Unit", values=(DimensionValue("USD"),)),
    )
    assert meta.source_organization == "World Bank"
    assert meta.official_url == "https://api.worldbank.org"
    assert meta.last_updated == "2026-01-01"
    assert meta.keywords == ("gdp", "per capita")
    assert meta.semantics == StatisticalSemantics(price_basis="nominal", currency="USD")


def test_search_without_rich_metadata_leaves_new_fields_none():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    results = catalog.search("population")
    assert results[0].dataset_id is None
    assert results[0].dimensions is None
    assert results[0].semantics is None


def test_catalog_migrates_an_on_disk_db_created_before_the_semantics_column():
    # Phase B shipped persistent on-disk catalogs before Phase F added
    # catalog_meta.semantics - opening an existing file built on the old
    # schema must not crash (see Catalog._migrate()).
    import sqlite3

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    old_schema = _SCHEMA.replace("    semantics TEXT,\n", "")
    assert "semantics" not in old_schema  # sanity: the replace actually did something
    connection.executescript(old_schema)
    connection.execute(
        "INSERT INTO catalog_meta (source_id, indicator_id, ingested_at) VALUES (?, ?, ?)",
        ("WB_WDI", "SP_POP_TOTL", "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()

    catalog = Catalog(connection)  # must not raise
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="NY_GDP_PCAP_CD",
                source_id="WB_WDI",
                names={"en": "GDP per capita"},
                semantics=StatisticalSemantics(price_basis="nominal"),
            )
        ]
    )

    meta = catalog.get("WB_WDI", "NY_GDP_PCAP_CD")
    assert meta.semantics == StatisticalSemantics(price_basis="nominal")


def test_get_returns_none_for_unknown_pair():
    catalog = Catalog()
    assert catalog.get("WB_WDI", "NOPE") is None


def test_get_returns_the_entry_by_exact_id():
    catalog = build_catalog(
        [IndicatorEntry(indicator_id="SP_POP_TOTL", source_id="WB_WDI", names={"en": "Population, total"})]
    )
    meta = catalog.get("WB_WDI", "SP_POP_TOTL")
    assert meta is not None
    assert meta.name == "Population, total"


def test_add_upserts_rather_than_duplicating_on_reingestion():
    catalog = build_catalog(
        [
            IndicatorEntry(
                indicator_id="SP_POP_TOTL",
                source_id="WB_WDI",
                names={"en": "Population, total"},
                unit="count",
            )
        ]
    )
    # Simulate a refresh discovering updated metadata for the same series.
    catalog.add(
        [
            IndicatorEntry(
                indicator_id="SP_POP_TOTL",
                source_id="WB_WDI",
                names={"en": "Population, total"},
                unit="persons",
            )
        ]
    )

    results = catalog.search("population")
    assert len(results) == 1
    assert results[0].unit == "persons"


def test_stats_counts_distinct_indicators_per_source():
    catalog = build_catalog(
        [
            IndicatorEntry(indicator_id="A", source_id="WB_WDI", names={"en": "A"}),
            IndicatorEntry(indicator_id="B", source_id="WB_WDI", names={"en": "B", "fr": "B fr"}),
            IndicatorEntry(indicator_id="C", source_id="ESTAT_NAMA_10_GDP", names={"en": "C"}),
        ]
    )
    assert catalog.stats() == {"ESTAT_NAMA_10_GDP": 1, "WB_WDI": 2}


def test_summary_reports_sources_datasets_indicators_and_last_refresh():
    catalog = build_catalog(
        [
            IndicatorEntry(
                indicator_id="A", source_id="WB_WDI", names={"en": "A"}, dataset_id="WDI"
            ),
            IndicatorEntry(
                indicator_id="B", source_id="WB_WDI", names={"en": "B"}, dataset_id="WDI"
            ),
            IndicatorEntry(
                indicator_id="C",
                source_id="ESTAT_NAMA_10_GDP",
                names={"en": "C"},
                dataset_id="NAMA_10_GDP",
            ),
        ]
    )

    summary = catalog.summary()

    assert summary["sources"] == 2
    assert summary["datasets"] == 2  # (WB_WDI, WDI) and (ESTAT_NAMA_10_GDP, NAMA_10_GDP)
    assert summary["indicators"] == 3
    assert summary["records_per_source"] == {"ESTAT_NAMA_10_GDP": 1, "WB_WDI": 2}
    assert summary["last_refresh_by_source"].keys() == {"WB_WDI", "ESTAT_NAMA_10_GDP"}
    assert summary["last_refresh"]  # a real timestamp, not None/empty


def test_summary_on_an_empty_catalog_reports_zeros_not_an_error():
    catalog = Catalog()

    summary = catalog.summary()

    assert summary == {
        "sources": 0,
        "datasets": 0,
        "indicators": 0,
        "records_per_source": {},
        "last_refresh": None,
        "last_refresh_by_source": {},
    }


def test_refreshing_the_same_entries_twice_does_not_duplicate_rows():
    # Phase B requirement: "running it twice must update existing entries
    # rather than creating duplicates" — checked at the actual storage
    # level (row counts), not just IngestionReport's added/updated/unchanged
    # labels (already covered separately in tests/test_ingestion.py).
    entries = [
        IndicatorEntry(indicator_id="A", source_id="WB_WDI", names={"en": "A"}, dataset_id="WDI"),
        IndicatorEntry(indicator_id="B", source_id="WB_WDI", names={"en": "B"}, dataset_id="WDI"),
    ]
    catalog = Catalog()

    catalog.add(entries)
    first_summary = catalog.summary()
    catalog.add(entries)  # same entries again, e.g. a second `ustat catalog refresh`
    second_summary = catalog.summary()

    assert first_summary["indicators"] == 2
    assert second_summary["indicators"] == 2  # not 4
    assert second_summary["records_per_source"] == {"WB_WDI": 2}
    assert catalog.search("A")
    assert len(catalog.search("A")) == 1  # one row, not a duplicate

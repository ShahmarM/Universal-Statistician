from __future__ import annotations

from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.providers.catalog_seed import CATALOG_SEED


def build_catalog(entries):
    catalog = Catalog()
    catalog.add(entries)
    return catalog


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
    from universal_statistician.providers.registry import SOURCES

    catalog = Catalog()
    catalog.add(CATALOG_SEED)

    for entry in CATALOG_SEED:
        # Every seeded indicator must belong to a real registry entry —
        # otherwise get_series() would reject it even though search() found it.
        assert entry.source_id in SOURCES

    results = catalog.search("population")
    assert any(r.indicator_id == "SP_POP_TOTL" for r in results)

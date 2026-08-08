"""Seed indicator metadata for the catalog, one list per registered dataset.

Populating this from each source's live codelist/conceptscheme (so the
catalog covers *every* indicator a dataflow offers, not just a hand-picked
few) needs network access this sandbox doesn't have — see registry.py's
module docstring. Until then, the catalog is seeded with the indicators this
project has already verified end to end in get_series() (same codes as the
registry's key-format tests), so search results are guaranteed queryable, not
just guessed labels.

English labels are standard usage for these codes. The Eurostat entry also
carries French and German, Eurostat's other two official documentation
languages for national-accounts labels — verified terminology, not machine
translation.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry

CATALOG_SEED: list[IndicatorEntry] = [
    IndicatorEntry(
        indicator_id="SP_POP_TOTL",
        source_id="WB_WDI",
        names={"en": "Population, total"},
        description="Total population, all World Bank WDI reporting countries.",
    ),
    IndicatorEntry(
        indicator_id="CP01",
        source_id="IMF_DATA_CPI",
        names={"en": "Food and non-alcoholic beverages (CPI, COICOP CP01)"},
        description="Consumer price index for the food and non-alcoholic beverages category.",
    ),
    IndicatorEntry(
        indicator_id="B1GQ",
        source_id="ESTAT_NAMA_10_GDP",
        names={
            "en": "Gross domestic product at market prices",
            "fr": "Produit intérieur brut aux prix du marché",
            "de": "Bruttoinlandsprodukt zu Marktpreisen",
        },
        description="GDP, current prices, million EUR (na_item=B1GQ, unit=CP_MEUR).",
    ),
    IndicatorEntry(
        indicator_id="000007SF",
        source_id="SCB_TAB6471",
        names={"en": "Statistics Sweden (SCB) table TAB6471, content code 000007SF"},
        # Honest gap, not a guess: no public documentation of what this
        # specific content code measures was found (see providers/pxweb_registry.py) —
        # only that it's a verified, queryable code (from pxwebpy's own test suite),
        # not that we know what it represents.
        description=(
            "Verified queryable via pxwebpy's own test suite; the statistical "
            "concept this content code represents is not confirmed."
        ),
    ),
]

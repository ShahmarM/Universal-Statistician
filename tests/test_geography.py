"""Tests for core/geography.py — added in Phase G after a real, live run of
AnthropicPlanner against real questions showed every one of 10 example
questions failing retrieval because the model wrote a country NAME
("Azerbaijan") rather than the code a provider's ref_area needs. See
docs/benchmarks/phase-g-live-planner-report.md for the full before/after.
"""

from __future__ import annotations

from universal_statistician.core.geography import provider_ref_area, resolve_geography


def test_resolve_geography_converts_a_country_name_to_alpha_3():
    assert resolve_geography("Azerbaijan") == "AZE"
    assert resolve_geography("Georgia") == "GEO"
    assert resolve_geography("Kazakhstan") == "KAZ"


def test_resolve_geography_is_idempotent_on_an_already_correct_code():
    assert resolve_geography("AZE") == "AZE"


def test_resolve_geography_normalizes_an_alpha_2_code_to_alpha_3():
    assert resolve_geography("AZ") == "AZE"


def test_resolve_geography_returns_unresolvable_input_unchanged_not_none():
    # Never silently drop a geography the caller can still try - retrieval/
    # validation should get an honest "not found" for a genuinely unknown
    # value, not have it vanish here.
    assert resolve_geography("Not A Real Country") == "Not A Real Country"


def test_provider_ref_area_defaults_to_the_canonical_alpha_3_form():
    # World Bank/IMF's SDMX ref_area is alpha-3 (registry.py's own worked
    # examples: "AFG", "111"->CL_COUNTRY) - the default for any source not
    # explicitly listed as needing alpha-2.
    assert provider_ref_area("AZE", source_id="WB_WDI") == "AZE"
    assert provider_ref_area("AZE", source_id="IMF_DATA_CPI") == "AZE"


def test_provider_ref_area_converts_to_alpha_2_for_eurostat():
    # Eurostat's `geo` dimension is alpha-2 (registry.py's own worked
    # example: geo="LU").
    assert provider_ref_area("AZE", source_id="ESTAT_NAMA_10_GDP") == "AZ"
    assert provider_ref_area("LUX", source_id="ESTAT_NAMA_10_GDP") == "LU"


def test_provider_ref_area_returns_input_unchanged_when_unresolvable():
    assert provider_ref_area("Not A Real Country", source_id="ESTAT_NAMA_10_GDP") == "Not A Real Country"

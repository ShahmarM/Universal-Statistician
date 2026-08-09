"""Statistics Norway (SSB) via PXWebProvider — Phase J.

Deliberately small: PXWebProvider's get_series()/discover_catalog_entries()
are already fully generic across PX-Web agencies (see pxweb_provider.py's
module docstring — "every PX-Web source registered here gets discovery for
free"), and that machinery is already exercised offline by
test_pxweb_provider.py's SCB-based tests. What's specific to this new
registry entry — and what those tests can't cover — is whether SSB's real
API actually returns what pxweb_registry.py's SSB_09189 entry claims it
does, so this file is just that: a registry sanity check plus the two live
smoke tests.
"""

from __future__ import annotations

import pytest

from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.pxweb_provider import PXWebProvider
from universal_statistician.providers.pxweb_registry import PXWEB_SOURCES


def test_ssb_09189_is_registered_and_metadata_discoverable():
    provider = PXWebProvider(PXWEB_SOURCES["SSB_09189"])
    assert isinstance(provider, MetadataDiscoverable)
    assert provider.describe() == {
        "source_id": "SSB",
        "source_name": "Statistics Norway (SSB)",
        "table_id": "09189",
        "website": PXWEB_SOURCES["SSB_09189"].website,
    }


@pytest.mark.network
def test_live_ssb_gdp_smoke():
    """Real call against the live SSB (Statistics Norway) API. Live-verified
    during Phase J: "bnpb.nr23_9" (Gross domestic product, market values) at
    current prices ("Priser") returned NOK 5,935,035 million for 2022 —
    recorded here as the regression value."""
    provider = PXWebProvider(PXWEB_SOURCES["SSB_09189"])
    result = provider.get_series("bnpb.nr23_9", "Priser", start_period="2022", end_period="2022")
    obs = {o.period: o.value for o in result.observations}
    assert obs.get("2022") == pytest.approx(5_935_035.0)


@pytest.mark.network
def test_live_ssb_discovery_matches_the_documented_table_shape():
    """Real call against the live SSB API — confirms table 09189 still has
    the "Makrost" macroeconomic-indicator variable with the flagship GDP
    code, not just that some entries were discovered."""
    entries = PXWebProvider(PXWEB_SOURCES["SSB_09189"]).discover_catalog_entries()
    by_id = {e.indicator_id: e for e in entries}
    assert "bnpb.nr23_9" in by_id
    assert by_id["bnpb.nr23_9"].names["en"] == "Gross domestic product, market values"

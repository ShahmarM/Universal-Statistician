"""World Bank provider: SDMXProvider's data retrieval plus catalog
discovery.

A subclass rather than a change to SDMXProvider, so plain SDMX sources
don't start claiming MetadataDiscoverable support by sharing a base class.
World Bank's discovery is its own REST API, not an SDMX structure request
— see worldbank_discovery.py.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_provider import SDMXProvider
from universal_statistician.providers.worldbank_discovery import discover_wb_wdi_entries


class WorldBankProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        return discover_wb_wdi_entries()

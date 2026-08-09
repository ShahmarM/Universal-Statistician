"""World Bank provider: SDMXProvider's data retrieval plus catalog discovery.

A subclass of SDMXProvider — not a change to it — so IMF and Eurostat (also
SDMXProvider instances, per registry.py) don't accidentally start claiming
MetadataDiscoverable support just by sharing the base class. Each source's
discovery mechanism is genuinely different (see worldbank_discovery.py's
docstring on why World Bank's is its own bespoke REST API, not an SDMX
structure request) and gets added source by source, per phase, not by
widening the shared SDMXProvider.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_provider import SDMXProvider
from universal_statistician.providers.worldbank_discovery import discover_wb_wdi_entries


class WorldBankProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        return discover_wb_wdi_entries()

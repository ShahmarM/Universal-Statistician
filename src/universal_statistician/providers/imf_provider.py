"""IMF provider: SDMXProvider's data retrieval plus catalog discovery via
the CPI dataflow's own Data Structure Definition (DSD).

Unlike World Bank, IMF has a verified SDMX structure-discovery path, so
this uses genuine structure requests rather than a bespoke REST API.
sdmx1 defaults such a request to `?references=all`, resolving each
dimension's codelist inline, and IMF's response resolves directly — no
external-reference stub to follow up on as Eurostat needs.

Turning the resolved DSD into IndicatorEntry objects is shared with
EurostatProvider — see sdmx_discovery.py.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_discovery import SDMXDiscoveryError, entries_from_dsd
from universal_statistician.providers.sdmx_provider import SDMXProvider

__all__ = ["IMFProvider", "IMFDiscoveryError"]

#: Re-exported for backward compatibility with callers/tests written against
#: the original IMF-specific name; identical to the shared error type.
IMFDiscoveryError = SDMXDiscoveryError


class IMFProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        if not self.config.structure_id:
            raise SDMXDiscoveryError(
                f"{self.config.dataflow_id!r} has no structure_id configured for discovery."
            )

        message = self._client.get("datastructure", resource_id=self.config.structure_id)
        dsd = message.structure[self.config.structure_id]

        return entries_from_dsd(dsd, self.config, source_organization=self.source_name)

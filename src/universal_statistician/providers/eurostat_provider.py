"""Eurostat provider: SDMXProvider's data retrieval plus catalog discovery,
generalized beyond the single hard-coded NAMA_10_GDP/B1GQ seed entry.

Eurostat-specific quirk, per sdmx1's own live-exercised test: even with
`?references=all`, a `dataflow` response's `.structure` is only a stub
reference, so resolving the real DSD takes a second request:

    dsd = client.dataflow(resource_id="NAMA_10_GDP").dataflow["NAMA_10_GDP"].structure
    if dsd.is_external_reference:
        dsd = client.get(resource=dsd).structure[0]

Once resolved, mapping the DSD onto catalog entries is shared with
IMFProvider — see sdmx_discovery.py.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_discovery import entries_from_dsd
from universal_statistician.providers.sdmx_provider import SDMXProvider


class EurostatProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        dataflow_message = self._client.get("dataflow", resource_id=self.config.dataflow_id)
        dsd = dataflow_message.dataflow[self.config.dataflow_id].structure

        if dsd.is_external_reference:
            structure_message = self._client.get(resource=dsd)
            dsd = next(iter(structure_message.structure.values()))

        return entries_from_dsd(dsd, self.config, source_organization=self.source_name)

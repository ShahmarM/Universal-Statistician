"""OECD provider: SDMXProvider retrieval plus catalog discovery from the
National Accounts (NAMAIN10) DSD, whose `datastructure` request resolves
inline (no external-reference follow-up, unlike Eurostat).

Scoped to this one live-verified dataflow out of OECD's ~1,500. Its DSD
has 12 non-time dimensions, and several codelist-valid combinations return
404 — the working key was found by live wildcard probing and cross-checked
against World Bank's GDP figure, not inferred from codelists. Discovery
enumerates TRANSACTION codes the codelist advertises; not every code has
data for every country under this dataflow's fixed dimensions, which is a
retrieval concern, not a discovery bug.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_discovery import SDMXDiscoveryError, entries_from_dsd
from universal_statistician.providers.sdmx_provider import SDMXProvider


class OECDProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        if not self.config.structure_id:
            raise SDMXDiscoveryError(
                f"{self.config.dataflow_id!r} has no structure_id configured for discovery."
            )

        message = self._client.get("datastructure", resource_id=self.config.structure_id)
        dsd = message.structure[self.config.structure_id]

        return entries_from_dsd(dsd, self.config, source_organization=self.source_name)

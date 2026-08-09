"""IMF provider: SDMXProvider's data retrieval plus catalog discovery via
the CPI dataflow's own Data Structure Definition (DSD).

Unlike World Bank (Phase 2, providers/worldbank_discovery.py), IMF genuinely
has a verified SDMX *structure*-discovery path: sdmx1's own integration test
suite (sdmx/tests/test_sources.py::TestIMF_DATA) declares
`"structure": dict(resource_id="DSD_CPI")` and
`"codelist": dict(resource_id="CL_COUNTRY")` as real, exercised IMF_DATA
endpoints — so this uses genuine SDMX structure requests, not a bespoke REST
API, for exactly the same "don't guess, use verified ground truth" reason
this project applies everywhere else.

sdmx1 defaults a `datastructure` request (given a specific resource_id) to
`?references=all`, resolving every dimension's referenced codelist inline —
this is the library's own documented default (sdmx/rest/v21.py's
handle_structure()), not a query parameter this module invents. Unlike
Eurostat (Phase 4, eurostat_provider.py), IMF's structure response resolves
directly — no external-reference stub to follow up on.

Turning the resolved DSD into IndicatorEntry objects is shared with
EurostatProvider — see sdmx_discovery.py's module docstring for why that's
factored out rather than duplicated.

Honesty check, consistent with every other source: this has not been
exercised against the live API from this sandbox (network blocked).
Discovery logic is unit-tested against a StructureMessage built from real
sdmx.model.v21 classes (DataStructureDefinition/Dimension/Codelist/Item),
the same in-memory-object approach tests/conftest.py already uses for data
messages — not a hand-guessed response shape. The one thing that needs a
live call (whether DSD_CPI's real response still matches this shape) is
covered only by the `network`-marked test.
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

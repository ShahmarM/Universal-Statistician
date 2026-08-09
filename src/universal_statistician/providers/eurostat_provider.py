"""Eurostat provider: SDMXProvider's data retrieval plus catalog discovery,
generalized beyond the single hard-coded NAMA_10_GDP/B1GQ seed entry.

Ground truth: sdmx1's own integration test (sdmx/tests/test_sources.py::
TestESTAT.test_ss_data) — the exact test registry.py already cites for this
dataflow's key format — walks precisely this discovery path against the
live API:

    dsd = client.dataflow(resource_id="NAMA_10_GDP").dataflow["NAMA_10_GDP"].structure
    if dsd.is_external_reference:
        dsd = client.get(resource=dsd).structure[0]

That comment ("Even with ?references=all, ESTAT returns a short message
with the DSD as an external reference. Query again to get its actual
contents.") documents a real, Eurostat-specific server quirk: a `dataflow`
response's `.structure` is only a *stub* reference to the DSD, not the DSD
itself, so a second request is needed to resolve it. This is a real,
maintainer-observed behavior copied from the library's own test, not
guessed — the same principle already applied to every other source's key
format in this project.

Once resolved, mapping the DSD onto catalog entries is shared with
IMFProvider — see sdmx_discovery.py's module docstring.

Honesty check, consistent with every other source: this has not been
exercised against the live API from this sandbox (network blocked).
Discovery is unit-tested against a StructureMessage sequence built from real
sdmx.model.v21 classes reproducing the external-reference-then-resolve
pattern above, not a hand-guessed shape. The one thing that needs a live
call (whether the real response still matches this shape) is covered only
by the `network`-marked test.
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

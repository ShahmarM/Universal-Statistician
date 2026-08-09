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

Rather than separately guess which DSD dimension is "the indicator
dimension", this reuses the *same* key_dimensions placeholder positions
already verified for get_series() (providers/registry.py): the "{indicator}"
position in key_dimensions is defined to be that dimension's position in the
DSD's own order (registry.py's docstring: "SDMX key dimensions in this
dataflow's DSD order"). Discovery and retrieval are therefore provably
consistent with each other by construction — one verified fact
(key_dimensions), not two independently-guessed ones — and a DSD response
whose dimension count doesn't match key_dimensions' length raises rather
than silently mis-mapping a dimension (see IMFDiscoveryError).

sdmx1 defaults a `datastructure` request (given a specific resource_id) to
`?references=all`, resolving every dimension's referenced codelist inline —
this is the library's own documented default (sdmx/rest/v21.py's
handle_structure()), not a query parameter this module invents.

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
from universal_statistician.providers.sdmx_provider import SDMXProvider


class IMFDiscoveryError(RuntimeError):
    pass


class IMFProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        if not self.config.structure_id:
            raise IMFDiscoveryError(
                f"{self.config.dataflow_id!r} has no structure_id configured for discovery."
            )

        message = self._client.get("datastructure", resource_id=self.config.structure_id)
        dsd = message.structure[self.config.structure_id]

        regular_dims = sorted(
            (d for d in dsd.dimensions if type(d).__name__ != "TimeDimension"),
            key=lambda d: d.order,
        )
        if len(regular_dims) != len(self.config.key_dimensions):
            raise IMFDiscoveryError(
                f"DSD {self.config.structure_id!r} has {len(regular_dims)} non-time "
                f"dimensions but key_dimensions declares {len(self.config.key_dimensions)} — "
                "refusing to guess which one is the indicator dimension."
            )

        indicator_dim = regular_dims[self.config.key_dimensions.index("{indicator}")]
        indicator_codelist = _enumerated_codelist(indicator_dim)
        if indicator_codelist is None:
            raise IMFDiscoveryError(
                f"Indicator dimension {indicator_dim.id!r} has no enumerated codelist "
                "in the DSD response."
            )

        geographic_coverage = None
        if "{ref_area}" in self.config.key_dimensions:
            ref_area_dim = regular_dims[self.config.key_dimensions.index("{ref_area}")]
            ref_area_codelist = _enumerated_codelist(ref_area_dim)
            if ref_area_codelist is not None:
                geographic_coverage = tuple(sorted(ref_area_codelist.items))

        entries = []
        for code_id, item in indicator_codelist.items.items():
            entries.append(
                IndicatorEntry(
                    indicator_id=code_id,
                    source_id=self.config.registry_id,
                    names={"en": str(item.name)},
                    description=str(item.description) if item.description else None,
                    dataset_id=self.config.dataflow_id,
                    frequency=self._frequency(),
                    geographic_coverage=geographic_coverage,
                    source_organization=self.source_name,
                    official_url=self.config.website,
                )
            )
        return entries


def _enumerated_codelist(dimension):
    representation = dimension.local_representation
    return representation.enumerated if representation is not None else None

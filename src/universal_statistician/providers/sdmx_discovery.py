"""Shared logic for turning a resolved SDMX DataStructureDefinition into
IndicatorEntry objects, reused by every SDMX source with a *verified*
structure-discovery path (currently IMFProvider, EurostatProvider).

Fetching the DSD itself is source-specific — IMF's is one direct
`datastructure` request (imf_provider.py); Eurostat's dataflow response
returns the DSD as an external-reference stub that needs a second request to
resolve (eurostat_provider.py), a real quirk documented in sdmx1's own test
suite. Once resolved, mapping the DSD onto catalog entries is identical for
both: reuse the already-verified key_dimensions placeholder position (see
registry.py's docstring) to find "the indicator dimension" and "the ref_area
dimension" — never a guessed dimension ID string.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.registry import SDMXSourceConfig


class SDMXDiscoveryError(RuntimeError):
    pass


def _enumerated_codelist(dimension):
    """The dimension's enumerated codelist, wherever the live DSD actually
    put it — verified to differ by source (Phase H, live `pytest -m
    network` run): Eurostat's NAMA_10_GDP puts a real, non-empty codelist
    directly on each dimension's own `local_representation` (already
    covered by the offline fixture this project built before any live
    access existed, and confirmed live). IMF's DSD_CPI does not — every one
    of its dimensions' `local_representation` came back `None` from a live
    `datastructure` request, and the real, fully-populated codelist (343
    country codes, 15 COICOP categories, etc.) was only reachable via each
    dimension's *concept*'s `core_representation` instead. Checked here as
    a fallback, in that order, rather than assumed for either source."""
    representation = dimension.local_representation
    codelist = representation.enumerated if representation is not None else None
    if codelist is not None and codelist.items:
        return codelist

    concept = dimension.concept_identity
    if concept is not None and concept.core_representation is not None:
        concept_codelist = concept.core_representation.enumerated
        if concept_codelist is not None and concept_codelist.items:
            return concept_codelist

    return None


def entries_from_dsd(dsd, config: SDMXSourceConfig, *, source_organization: str) -> list[IndicatorEntry]:
    """dsd: a resolved (non-external-reference) sdmx.model DataStructureDefinition.

    Raises SDMXDiscoveryError rather than silently mis-mapping when the DSD's
    dimension count doesn't match config.key_dimensions, or the indicator
    dimension has no enumerated codelist to enumerate codes from.
    """
    regular_dims = sorted(
        (d for d in dsd.dimensions if type(d).__name__ != "TimeDimension"),
        key=lambda d: d.order,
    )
    if len(regular_dims) != len(config.key_dimensions):
        raise SDMXDiscoveryError(
            f"DSD {dsd.id!r} has {len(regular_dims)} non-time dimensions but "
            f"key_dimensions declares {len(config.key_dimensions)} — refusing to "
            "guess which one is the indicator dimension."
        )

    indicator_dim = regular_dims[config.key_dimensions.index("{indicator}")]
    indicator_codelist = _enumerated_codelist(indicator_dim)
    if indicator_codelist is None:
        raise SDMXDiscoveryError(
            f"Indicator dimension {indicator_dim.id!r} has no enumerated codelist "
            "in the DSD response."
        )

    geographic_coverage = None
    if "{ref_area}" in config.key_dimensions:
        ref_area_dim = regular_dims[config.key_dimensions.index("{ref_area}")]
        ref_area_codelist = _enumerated_codelist(ref_area_dim)
        if ref_area_codelist is not None:
            geographic_coverage = tuple(sorted(ref_area_codelist.items))

    frequency = next((d for d in config.key_dimensions if d in {"A", "Q", "M", "D"}), None)

    entries = []
    for code_id, item in indicator_codelist.items.items():
        entries.append(
            IndicatorEntry(
                indicator_id=code_id,
                source_id=config.registry_id,
                names={"en": str(item.name)},
                description=str(item.description) if item.description else None,
                dataset_id=config.dataflow_id,
                unit=config.unit_label,
                frequency=frequency,
                geographic_coverage=geographic_coverage,
                source_organization=source_organization,
                official_url=config.website,
                semantics=config.semantics,
            )
        )
    return entries

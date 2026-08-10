"""Shared logic for turning a resolved SDMX DataStructureDefinition into
IndicatorEntry objects, reused by every SDMX source with a verified
structure-discovery path.

Fetching the DSD is source-specific (IMF/OECD resolve it inline; Eurostat
returns an external-reference stub needing a second request), but mapping
it onto catalog entries is identical: the indicator and ref_area
dimensions are found by their verified key_dimensions placeholder
position, never a guessed dimension ID.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.registry import SDMXSourceConfig


class SDMXDiscoveryError(RuntimeError):
    pass


def _enumerated_codelist(dimension):
    """The dimension's enumerated codelist, wherever the live DSD put it:
    Eurostat populates `local_representation`, IMF leaves it None and
    exposes the codelist via the dimension's concept instead. Both are
    checked, in that order, rather than assumed."""
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

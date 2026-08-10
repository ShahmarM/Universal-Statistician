"""Geography name/code resolution via pycountry. LLM planners routinely
write country names ("Azerbaijan") where a code is needed, so resolution
is structural, not prompt-dependent. resolve_geography() picks one
canonical form (alpha-3) used everywhere; provider_ref_area() converts to
a source's own format only at the retrieval boundary.
"""

from __future__ import annotations

from functools import lru_cache

import pycountry

#: Registry keys whose ref_area dimension is ISO 3166-1 alpha-2.
ALPHA_2_SOURCES = {"ESTAT_NAMA_10_GDP"}


@lru_cache(maxsize=1024)
def resolve_geography(value: str) -> str:
    """Resolve a country name/code to canonical alpha-3. Returns `value`
    unchanged, never raises, when unresolvable — retrieval/validation
    should still see it and report an honest "not found". Cached: called
    from per-cell/per-column loops, and pycountry lookups aren't free."""
    try:
        return pycountry.countries.lookup(value).alpha_3
    except LookupError:
        return value


@lru_cache(maxsize=1024)
def _to_alpha_2(canonical_geo: str) -> str:
    try:
        return pycountry.countries.lookup(canonical_geo).alpha_2
    except LookupError:
        return canonical_geo


def provider_ref_area(canonical_geo: str, *, source_id: str) -> str:
    """Convert a canonical alpha-3 geography into the code format
    `source_id`'s ref_area dimension expects."""
    return _to_alpha_2(canonical_geo) if source_id in ALPHA_2_SOURCES else canonical_geo

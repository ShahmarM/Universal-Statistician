"""Geography name/code resolution (Phase G).

Found live, not hypothetically: running AnthropicPlanner against real
questions ("What is the population of Azerbaijan?") showed the model
routinely returns a country NAME ("Azerbaijan") rather than the ISO code a
provider's `ref_area` actually needs — every one of 10 example questions
failed retrieval for exactly this reason before this module existed. The
planner's own system prompt already tells it either form is acceptable
(section 11's "in whatever form the user used them"), so the fix has to be
structural, not a hope that better prompting alone makes every model
comply — the same principle already applied to indicator codes (never
trust the LLM, always resolve through code).

Resolved via `pycountry` — the same "don't write your own X" principle
already applied to sdmx1 (SDMX) and pxwebpy (PX-Web) for their protocols,
rather than a hand-rolled name->code dict that would need constant upkeep.

Different sources use different ISO forms for the same country: World Bank
and IMF's SDMX `ref_area` is alpha-3 ("AFG" — see registry.py's SOURCES),
Eurostat's `geo` dimension is alpha-2 ("LU", also registry.py's own worked
example). So there are two functions here, not one: `resolve_geography()`
picks one canonical identity (alpha-3) to use everywhere in a QueryPlan —
selection scoring, column keys, validation, chart labels — regardless of
which source ends up serving it; `provider_ref_area()` converts that
canonical form into whatever code the specific source being queried
actually expects, applied only at the retrieval call boundary
(core/ask.py::_fetch_table), not baked into the plan itself.
"""

from __future__ import annotations

import pycountry

#: Registry keys whose ref_area dimension is ISO 3166-1 alpha-2, not the
#: alpha-3 every other currently-registered source uses. Extend this set
#: alongside any new source whose worked example (registry.py) uses alpha-2.
ALPHA_2_SOURCES = {"ESTAT_NAMA_10_GDP"}


def resolve_geography(value: str) -> str:
    """Best-effort resolve a country name or code to its canonical ISO
    3166-1 alpha-3 form (e.g. "Azerbaijan" -> "AZE", "az" -> "AZE",
    "AZE" -> "AZE"). Returns `value` unchanged, never raises, when
    pycountry can't resolve it — an unresolvable geography should still
    reach retrieval/validation and produce an honest "not found" there,
    not silently vanish here."""
    try:
        return pycountry.countries.lookup(value).alpha_3
    except LookupError:
        return value


def provider_ref_area(canonical_geo: str, *, source_id: str) -> str:
    """Convert a canonical alpha-3 geography (see resolve_geography) into
    the code format `source_id`'s ref_area dimension actually expects."""
    if source_id not in ALPHA_2_SOURCES:
        return canonical_geo
    try:
        return pycountry.countries.lookup(canonical_geo).alpha_2
    except LookupError:
        return canonical_geo

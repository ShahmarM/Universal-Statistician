"""Shared upstream HTTP timeout configuration (Phase K).

Every provider that talks to an external API already had a *default*
timeout before this: sdmx1's own `Session` class defaults to 30s,
`pxweb.PxApi` defaults to 30s, and CensusProvider/worldbank_discovery
already passed `timeout=30` explicitly to `requests`. None of them could
hang forever even before Phase K. What was missing was a way to *tune*
that number for a real deployment (a slow network, or a stricter SLA)
without editing source — one shared env var, read once here, used
consistently by every provider instead of each hardcoding its own 30.
"""

from __future__ import annotations

import os

DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 30.0


def upstream_timeout_seconds() -> float:
    raw = os.environ.get("USTAT_UPSTREAM_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    return float(raw)

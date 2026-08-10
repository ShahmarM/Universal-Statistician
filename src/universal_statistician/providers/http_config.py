"""Shared upstream HTTP timeout, tunable via USTAT_UPSTREAM_TIMEOUT_SECONDS
so a deployment can adjust it without editing source. Every provider reads
it here rather than hardcoding its own default.
"""

from __future__ import annotations

import os

DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 30.0


def upstream_timeout_seconds() -> float:
    raw = os.environ.get("USTAT_UPSTREAM_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    return float(raw)

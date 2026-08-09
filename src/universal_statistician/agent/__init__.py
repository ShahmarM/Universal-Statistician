"""Iterative statistical-investigation agent (see docs/architecture/agent-migration-note.md).

The LLM controls investigation strategy and interpretation; every number,
identifier, and calculation is produced by deterministic code in
agent/tools.py (wrapping the same catalog/provider/compose/validation/
provenance modules core/ask.py's legacy single-pass path already used).
"""

from __future__ import annotations

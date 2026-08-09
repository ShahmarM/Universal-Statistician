from __future__ import annotations

from universal_statistician.providers.http_config import upstream_timeout_seconds


def test_defaults_to_30_seconds(monkeypatch):
    monkeypatch.delenv("USTAT_UPSTREAM_TIMEOUT_SECONDS", raising=False)
    assert upstream_timeout_seconds() == 30.0


def test_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("USTAT_UPSTREAM_TIMEOUT_SECONDS", "45")
    assert upstream_timeout_seconds() == 45.0

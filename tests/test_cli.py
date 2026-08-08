from __future__ import annotations

import json

from typer.testing import CliRunner

from universal_statistician import cli
from universal_statistician.core.engine import QueryEngine

from .helpers import LookupProvider, make_series

runner = CliRunner()


def test_sources_lists_registered_datasets():
    result = runner.invoke(cli.app, ["sources"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {"WB_WDI", "IMF_DATA", "ESTAT"} <= {s["source_id"] for s in payload}


def test_source_describes_one_dataset():
    result = runner.invoke(cli.app, ["source", "WB_WDI"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["source_id"] == "WB_WDI"


def test_source_unknown_id_exits_nonzero_with_clean_message():
    result = runner.invoke(cli.app, ["source", "NOPE"])
    assert result.exit_code == 1
    assert "Unknown source" in result.stderr
    assert "'Unknown source" not in result.stderr  # not double-quoted via KeyError repr


def test_search_finds_seeded_indicator():
    result = runner.invoke(cli.app, ["search", "population"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert any(item["indicator_id"] == "SP_POP_TOTL" for item in payload)


def test_compare_with_no_query_shape_exits_nonzero():
    result = runner.invoke(cli.app, ["compare", "WB_WDI"])
    assert result.exit_code == 1
    assert "cross-country" in result.stderr


def test_chat_without_api_key_exits_cleanly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["chat"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.stderr


def test_series_and_compare_against_a_fake_engine(monkeypatch):
    provider = LookupProvider(
        "FAKE",
        {
            ("POP", "AFG"): make_series("POP", "AFG", {"2020": 10.0}),
            ("POP", "USA"): make_series("POP", "USA", {"2020": 300.0}),
        },
    )
    monkeypatch.setattr(cli, "_engine", QueryEngine({"FAKE": provider}))

    series_result = runner.invoke(cli.app, ["series", "FAKE", "POP", "AFG"])
    assert series_result.exit_code == 0
    assert json.loads(series_result.stdout)["ref_area"] == "AFG"

    compare_result = runner.invoke(
        cli.app,
        ["compare", "FAKE", "--indicator", "POP", "--ref-area", "AFG", "--ref-area", "USA"],
    )
    assert compare_result.exit_code == 0
    payload = json.loads(compare_result.stdout)
    assert [c["key"] for c in payload["columns"]] == ["AFG", "USA"]

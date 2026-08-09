"""fastapi.testclient.TestClient drives requests through the full Starlette
stack, including its threadpool for sync endpoints — the same kind of
cross-thread execution that broke Catalog/Cache before mcp_server.py's fix,
so this doubles as regression coverage for that fix from a second interface.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from universal_statistician import api
from universal_statistician.core.catalog import Catalog, IndicatorEntry
from universal_statistician.core.engine import QueryEngine

from .helpers import FailingProvider, LookupProvider, make_series

client = TestClient(api.app)


def test_list_sources_returns_registered_datasets():
    response = client.get("/sources")
    assert response.status_code == 200
    source_ids = {row["source_id"] for row in response.json()}
    assert {"WB_WDI", "IMF_DATA", "ESTAT"} <= source_ids


def test_describe_source_returns_one_dataset():
    response = client.get("/sources/WB_WDI")
    assert response.status_code == 200
    assert response.json()["source_id"] == "WB_WDI"


def test_describe_source_unknown_id_is_404_with_clean_message():
    response = client.get("/sources/NOPE")
    assert response.status_code == 404
    assert response.json()["detail"].startswith("Unknown source")


def test_search_runs_through_the_threadpool_successfully():
    # Regression coverage: Starlette runs sync endpoints in a worker thread,
    # same class of bug as the MCP server's cross-thread sqlite3 issue.
    response = client.get("/search", params={"q": "population"})
    assert response.status_code == 200
    assert any(row["indicator_id"] == "SP_POP_TOTL" for row in response.json())


def test_compare_with_no_query_shape_is_400():
    response = client.post("/compare", json={"source_id": "WB_WDI"})
    assert response.status_code == 400
    assert "cross-country" in response.json()["detail"]


def _with_fake_engine(monkeypatch):
    provider = LookupProvider(
        "FAKE",
        {
            ("POP", "AFG"): make_series("POP", "AFG", {"2020": 10.0}),
            ("POP", "USA"): make_series("POP", "USA", {"2020": 300.0}),
        },
    )
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": provider}))


def test_get_series_against_a_fake_engine(monkeypatch):
    _with_fake_engine(monkeypatch)
    response = client.get(
        "/series", params={"source_id": "FAKE", "indicator_id": "POP", "ref_area": "AFG"}
    )
    assert response.status_code == 200
    assert response.json()["ref_area"] == "AFG"


def test_compare_cross_country_against_a_fake_engine(monkeypatch):
    _with_fake_engine(monkeypatch)
    response = client.post(
        "/compare",
        json={
            "source_id": "FAKE",
            "indicator_id": "POP",
            "ref_areas": ["AFG", "USA"],
            "growth": True,
            "rank": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    keys = {c["key"] for c in payload["columns"]}
    assert {"AFG", "USA", "AFG__yoy_growth_pct", "AFG__rank"} <= keys


def test_provider_failure_is_a_clean_502_with_cors_headers(monkeypatch):
    # Regression test: a live browser run against a network-blocked SDMX host
    # showed that an *unhandled* provider exception doesn't just 500 — the
    # browser reports it as a CORS failure, because the response never
    # completes normally enough for CORSMiddleware to attach its headers.
    # _call()'s broad except Exception must turn this into a normal
    # HTTPException so CORS headers are still present.
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": FailingProvider()}))
    response = client.get(
        "/series",
        params={"source_id": "FAKE", "indicator_id": "POP", "ref_area": "AFG"},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 502
    assert "simulated upstream network failure" in response.json()["detail"]
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_plan_returns_an_inspectable_plan_without_retrieval():
    response = client.post("/plan", json={"question": "population"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == "population"
    assert payload["concepts"] == ["population"]
    assert payload["assumptions"]


def test_plan_with_use_llm_but_no_api_key_is_400(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post("/plan", json={"question": "population", "use_llm": True})
    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_ask_finds_a_catalog_candidate_but_cannot_retrieve_without_a_geography(monkeypatch):
    # The rule-based planner (default, no ANTHROPIC_API_KEY here) never
    # extracts a geography from question text — it only echoes the whole
    # question as one search concept. So /ask honestly reports "no
    # geography" rather than fabricating a country to query. Full retrieval
    # through /ask is exercised in test_ask.py against a scripted planner
    # that does supply geographies (unreachable via this HTTP surface,
    # which only offers RuleBasedPlanner vs AnthropicPlanner).
    provider = LookupProvider(
        "FAKE",
        {("POP", "AFG"): make_series("POP", "AFG", {"2019": 10.0, "2020": 11.0})},
    )
    catalog = Catalog()
    catalog.add([IndicatorEntry(indicator_id="POP", source_id="FAKE", names={"en": "Population"})])
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": provider}, catalog=catalog))

    response = client.post("/ask", json={"question": "population"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["table"] is None
    assert any("No geography" in w for w in payload["warnings"])
    assert payload["query_plan"]["selected_indicators"][0]["indicator_id"] == "POP"


def test_ask_with_use_llm_but_no_api_key_is_400(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post("/ask", json={"question": "population", "use_llm": True})
    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]

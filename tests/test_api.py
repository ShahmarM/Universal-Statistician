"""fastapi.testclient.TestClient drives requests through the full Starlette
stack, including its threadpool for sync endpoints — the same kind of
cross-thread execution that broke Catalog/Cache before mcp_server.py's fix,
so this doubles as regression coverage for that fix from a second interface.
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from fastapi.testclient import TestClient

from universal_statistician import api
from universal_statistician.agent.answer_writer import WRITE_ANSWER_TOOL_NAME, AnthropicAnswerWriter
from universal_statistician.agent.llm import AnthropicAgent
from universal_statistician.agent.state import catalog_id as agent_catalog_id
from universal_statistician.agent.verifier import VERIFY_TOOL_NAME, AnthropicVerifier
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


def test_ask_rejects_an_unknown_mode():
    response = client.post("/ask", json={"question": "population", "mode": "turbo"})
    assert response.status_code == 400
    assert "Unknown mode" in response.json()["detail"]


def test_ask_with_an_explicit_fast_override_never_touches_research_mode_even_with_a_research_question():
    # "Compare" is a research-signal phrase (agent/modes.py's auto heuristic
    # would pick research), but an explicit mode="fast" must always win
    # (task section 7) -- and with use_llm left False, no LLM agent is even
    # constructed, so this proves the override rather than a fallback.
    response = client.post("/ask", json={"question": "Compare population sources", "mode": "fast"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode_used"] == "fast"
    assert "debug" not in payload


def _agent_message(*blocks, stop_reason="end_turn") -> Message:
    return Message(
        id="msg_test", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason=stop_reason, stop_sequence=None, content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class _ScriptedAgentClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        return self._responses.pop(0)


class _ScriptedTextClient:
    """Single-response client shared by AnthropicAnswerWriter/AnthropicVerifier
    fakes -- each of those only ever makes one call per invocation."""

    def __init__(self, response) -> None:
        self._response = response
        self.messages = self

    def create(self, **kwargs):
        return self._response


def test_ask_runs_research_mode_and_exposes_a_debug_trail_when_requested(monkeypatch):
    provider = LookupProvider(
        "FAKE",
        {("POP", "AFG"): make_series("POP", "AFG", {"2019": 10.0, "2020": 11.0}, source_id="FAKE")},
    )
    catalog = Catalog()
    catalog.add([IndicatorEntry(indicator_id="POP", source_id="FAKE", names={"en": "Population"})])
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": provider}, catalog=catalog))

    cid = agent_catalog_id("FAKE", "POP")
    agent_client = _ScriptedAgentClient(
        [
            _agent_message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AFG"]},
                ),
                stop_reason="tool_use",
            ),
            _agent_message(TextBlock(type="text", text="Retrieved population for Afghanistan.")),
        ]
    )
    writer_client = _ScriptedTextClient(
        Message(
            id="msg_write", model="claude-sonnet-5", role="assistant", type="message",
            stop_reason="tool_use", stop_sequence=None,
            content=[
                ToolUseBlock(
                    type="tool_use", id="tu_write", name=WRITE_ANSWER_TOOL_NAME,
                    input={
                        "text": "Afghanistan's population was 11.0 in 2020.",
                        "citations": [
                            {"evidence_id": "result_1@2020", "stated_value": "11.0", "claimed_geography": "AFG"}
                        ],
                    },
                )
            ],
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    verifier_client = _ScriptedTextClient(
        Message(
            id="msg_verify", model="claude-sonnet-5", role="assistant", type="message",
            stop_reason="tool_use", stop_sequence=None,
            content=[ToolUseBlock(type="tool_use", id="tu_v", name=VERIFY_TOOL_NAME, input={"status": "PASS", "issues": []})],
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )

    def _fake_resolve_agent_components(use_llm):
        assert use_llm is True
        return (
            AnthropicAgent(client=agent_client),
            AnthropicAnswerWriter(client=writer_client),
            AnthropicVerifier(client=verifier_client),
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "_resolve_agent_components", _fake_resolve_agent_components)

    response = client.post(
        "/ask",
        json={"question": "Compare population sources for Afghanistan", "mode": "research", "use_llm": True, "debug": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode_used"] == "research"
    assert payload["answer"] == "Afghanistan's population was 11.0 in 2020."
    assert payload["verification"] == {"status": "PASS", "issues": []}
    assert payload["debug"]["tool_call_history"]
    assert payload["debug"]["tool_call_history"][0]["tool_name"] == "retrieve_series"
    assert "investigator_summary" in payload["debug"]


def test_ask_research_mode_without_debug_omits_the_debug_key(monkeypatch):
    provider = LookupProvider(
        "FAKE",
        {("POP", "AFG"): make_series("POP", "AFG", {"2019": 10.0, "2020": 11.0}, source_id="FAKE")},
    )
    catalog = Catalog()
    catalog.add([IndicatorEntry(indicator_id="POP", source_id="FAKE", names={"en": "Population"})])
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": provider}, catalog=catalog))

    cid = agent_catalog_id("FAKE", "POP")
    agent_client = _ScriptedAgentClient(
        [
            _agent_message(
                ToolUseBlock(
                    type="tool_use", id="tu_1", name="retrieve_series",
                    input={"catalog_id": cid, "geographies": ["AFG"]},
                ),
                stop_reason="tool_use",
            ),
            _agent_message(TextBlock(type="text", text="Done.")),
        ]
    )

    def _fake_resolve_agent_components(use_llm):
        return AnthropicAgent(client=agent_client), None, None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "_resolve_agent_components", _fake_resolve_agent_components)

    response = client.post(
        "/ask",
        json={"question": "Compare population sources for Afghanistan", "mode": "research", "use_llm": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode_used"] == "research"
    assert "debug" not in payload
    assert payload["verification"] is None


# ---- Phase K: production readiness -----------------------------------------


def test_health_reports_ok_and_catalog_stats_without_touching_any_provider(monkeypatch):
    # The whole point of /health (per this project's "startup never
    # requires upstream APIs" rule): even with every provider replaced by
    # one that raises on any call, /health must still succeed, because it
    # only reads the local catalog.
    monkeypatch.setattr(api, "_engine", QueryEngine({"FAKE": FailingProvider("FAKE")}, catalog=Catalog()))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "catalog" in body


def test_cors_kwargs_defaults_to_localhost_only(monkeypatch):
    monkeypatch.delenv("USTAT_CORS_ORIGINS", raising=False)
    assert api._cors_kwargs() == {
        "allow_origin_regex": r"http://(localhost|127\.0\.0\.1)(:\d+)?"
    }


def test_cors_kwargs_reads_a_comma_separated_env_var(monkeypatch):
    monkeypatch.setenv("USTAT_CORS_ORIGINS", "https://a.example.com, https://b.example.com")
    assert api._cors_kwargs() == {
        "allow_origins": ["https://a.example.com", "https://b.example.com"]
    }


def test_rate_limiter_allows_up_to_the_configured_max():
    limiter = api._RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False


def test_rate_limiter_tracks_clients_independently():
    limiter = api._RateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("5.6.7.8") is True  # a different client, own budget
    assert limiter.allow("1.2.3.4") is False


def test_rate_limiter_resets_after_the_window_elapses(monkeypatch):
    limiter = api._RateLimiter(max_requests=1, window_seconds=10)
    times = iter([100.0, 100.0, 111.0])
    monkeypatch.setattr(api.time, "monotonic", lambda: next(times))

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False  # still within the window
    assert limiter.allow("1.2.3.4") is True  # window elapsed, budget reset


def test_rate_limiter_disabled_when_max_requests_is_zero():
    limiter = api._RateLimiter(max_requests=0, window_seconds=60)
    assert all(limiter.allow("1.2.3.4") for _ in range(1000))


def test_rate_limit_middleware_returns_429_once_the_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(api, "_rate_limiter", api._RateLimiter(max_requests=1, window_seconds=60))

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 429

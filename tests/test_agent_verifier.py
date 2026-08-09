"""Independent verification pass (Phase 7).

Same fake-client-from-real-anthropic-types pattern as the rest of this
package's LLM-role tests: AnthropicVerifier is exercised through a
scripted forced tool-use response (mirrors
test_planning_anthropic_planner.py's approach for AnthropicPlanner, which
also uses tool_choice), and VerificationReport.from_tool_input() is tested
directly for its parsing/normalization behavior.
"""

from __future__ import annotations

from anthropic.types import Message, ToolUseBlock, Usage

from universal_statistician.agent.verifier import (
    ISSUE_CATEGORIES,
    VERIFY_TOOL_NAME,
    AnthropicVerifier,
    VerificationIssue,
    VerificationReport,
)


def _message(tool_input: dict) -> Message:
    return Message(
        id="msg_test", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason="tool_use", stop_sequence=None,
        content=[ToolUseBlock(type="tool_use", id="tu_1", name=VERIFY_TOOL_NAME, input=tool_input)],
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class FakeClient:
    def __init__(self, response: Message) -> None:
        self._response = response
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


# ---- VerificationReport.from_tool_input ------------------------------------


def test_from_tool_input_parses_a_clean_pass():
    report = VerificationReport.from_tool_input({"status": "PASS", "issues": []})
    assert report.status == "PASS"
    assert report.issues == ()


def test_from_tool_input_parses_issues_with_known_categories():
    report = VerificationReport.from_tool_input(
        {
            "status": "FAIL",
            "issues": [
                {"category": "price_basis_mismatch", "detail": "nominal vs real GDP compared directly"},
                {"category": "unsupported_number", "detail": "'99.9 billion' is not in the evidence"},
            ],
        }
    )
    assert report.status == "FAIL"
    assert report.issues == (
        VerificationIssue(category="price_basis_mismatch", detail="nominal vs real GDP compared directly"),
        VerificationIssue(category="unsupported_number", detail="'99.9 billion' is not in the evidence"),
    )


def test_from_tool_input_normalizes_status_case():
    report = VerificationReport.from_tool_input({"status": "warning", "issues": []})
    assert report.status == "WARNING"


def test_from_tool_input_treats_an_unparseable_status_as_fail_not_pass():
    report = VerificationReport.from_tool_input({"status": "not-sure", "issues": []})
    assert report.status == "FAIL"


def test_from_tool_input_treats_a_missing_status_as_fail_not_pass():
    report = VerificationReport.from_tool_input({"issues": []})
    assert report.status == "FAIL"


def test_from_tool_input_falls_back_an_unknown_category_to_unsupported_claim():
    report = VerificationReport.from_tool_input(
        {"status": "WARNING", "issues": [{"category": "made_up_category", "detail": "something"}]}
    )
    assert report.issues[0].category == "unsupported_claim"


def test_as_dict_round_trips():
    report = VerificationReport(status="WARNING", issues=(VerificationIssue(category="ignored_warning", detail="x"),))
    assert report.as_dict() == {"status": "WARNING", "issues": [{"category": "ignored_warning", "detail": "x"}]}


def test_issue_categories_is_closed_and_sorted_and_has_no_duplicates():
    assert list(ISSUE_CATEGORIES) == sorted(ISSUE_CATEGORIES)
    assert len(ISSUE_CATEGORIES) == len(set(ISSUE_CATEGORIES))


# ---- AnthropicVerifier -------------------------------------------------------


def test_anthropic_verifier_returns_a_parsed_report():
    client = FakeClient(_message({"status": "PASS", "issues": []}))
    verifier = AnthropicVerifier(client=client)

    report = verifier.verify(
        question="What was Azerbaijan's GDP in 2022?",
        evidence={"table": {"rows": [{"period": "2022", "gdp": 78.83}]}},
        draft_answer="Azerbaijan's GDP was 78.83 billion in 2022.",
    )

    assert report.status == "PASS"
    assert report.issues == ()


def test_anthropic_verifier_forces_the_verify_tool_and_passes_the_payload():
    client = FakeClient(_message({"status": "FAIL", "issues": [{"category": "unsupported_number", "detail": "x"}]}))
    verifier = AnthropicVerifier(client=client)

    report = verifier.verify(question="Q", evidence={"table": {}}, draft_answer="A draft")

    assert report.status == "FAIL"
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": VERIFY_TOOL_NAME}
    assert "tools" not in call or call["tools"][0]["name"] == VERIFY_TOOL_NAME
    assert "A draft" in call["messages"][0]["content"]

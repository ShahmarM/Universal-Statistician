"""Answer-writing pass + evidence-ID grounding guard (task section 2).

Same fake-client-from-real-anthropic-types pattern as the rest of this
package's LLM-role tests — AnthropicAnswerWriter is exercised through a
scripted forced tool-use response (mirrors test_planning_anthropic_planner.py
and test_agent_verifier.py, which also use tool_choice). The grounding
logic itself (check_citation/check_answer_grounding) is tested directly
against plain evidence dicts shaped exactly like
InvestigationState.evidence_package()["evidence"] — no LLM involved.
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from universal_statistician.agent.answer_writer import (
    WRITE_ANSWER_TOOL_NAME,
    AnswerDraft,
    AnswerWriteResult,
    AnthropicAnswerWriter,
    Citation,
    check_answer_grounding,
    check_citation,
    extract_numbers,
    write_and_verify_answer,
)
from universal_statistician.agent.evidence import VALUE_KINDS


def _message(*blocks, stop_reason: str = "end_turn") -> Message:
    return Message(
        id="msg_test", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason=stop_reason, stop_sequence=None, content=list(blocks),
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _tool_response(text: str, citations: list[dict]) -> Message:
    return _message(
        ToolUseBlock(
            type="tool_use", id="tu_1", name=WRITE_ANSWER_TOOL_NAME,
            input={"text": text, "citations": citations},
        ),
        stop_reason="tool_use",
    )


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._responses.pop(0)


class RaisingClient:
    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("network is down")


# One evidence entry for Azerbaijan's GDP, one derived growth entry, and a
# population entry for a second country — enough to exercise every check.
_EVIDENCE_INDEX = {
    "result_1@2022": {
        "evidence_id": "result_1@2022", "result_id": "result_1", "period": "2022",
        "value": 72.4, "unit": "current US$", "value_kind": "currency",
        "indicator_id": "NY_GDP_MKTP_CD", "geography": "AZE",
        "source_id": "WB_WDI", "source_name": "World Bank", "dataset_id": "WDI",
        "operation": None, "formula": None, "input_evidence_ids": [],
    },
    "result_2@2022": {
        "evidence_id": "result_2@2022", "result_id": "result_2", "period": "2022",
        "value": 10.5, "unit": "persons", "value_kind": "count",
        "indicator_id": "SP_POP_TOTL", "geography": "GEO",
        "source_id": "WB_WDI", "source_name": "World Bank", "dataset_id": "WDI",
        "operation": None, "formula": None, "input_evidence_ids": [],
    },
    "result_3@2022": {
        "evidence_id": "result_3@2022", "result_id": "result_3", "period": "2022",
        "value": 3.2, "unit": None, "value_kind": "percent",
        "indicator_id": None, "geography": None, "source_id": None, "source_name": None, "dataset_id": None,
        "operation": "growth", "formula": "(current - previous) / previous * 100",
        "input_evidence_ids": ["result_1@2021", "result_1@2022"],
    },
}

_EVIDENCE_PACKAGE = {"question": "q", "evidence": _EVIDENCE_INDEX}


# ---- extract_numbers ------------------------------------------------------


def test_extract_numbers_finds_plain_and_decimal_numbers():
    assert extract_numbers("GDP was 78.83 billion, up from 54.6.") == [(78.83, 2), (54.6, 1)]


def test_extract_numbers_handles_percentages():
    assert extract_numbers("Growth was 3.2%.") == [(3.2, 1)]


def test_extract_numbers_handles_comma_thousands():
    assert extract_numbers("Population was 10,400,000.") == [(10400000.0, 0)]


def test_extract_numbers_excludes_bare_four_digit_years():
    numbers = extract_numbers("In 2022, GDP was 78.83 billion, compared with 2021.")
    assert (78.83, 2) in numbers
    assert not any(value == 2022 for value, _ in numbers)


def test_extract_numbers_does_not_misread_a_digit_embedded_in_an_identifier():
    # Live-observed false positive: deterministic validation-finding text
    # routinely mentions column identifiers like "result_1"/"result_2" --
    # without a word-boundary guard, the trailing digit got misparsed as a
    # bare statistic ("1.0"), flagging text that never claimed any number
    # at all as containing an "ungrounded number".
    numbers = extract_numbers("Column 'result_1' has gap(s) in its annual coverage: [(1981, 1983), (1998, 2008)]")
    assert numbers == []


def test_extract_numbers_still_matches_a_real_number_next_to_an_identifier():
    numbers = extract_numbers("result_1 was 78.8 in 2022")
    assert numbers == [(78.8, 1)]


# ---- check_citation ---------------------------------------------------------


def test_check_citation_accepts_a_fully_compatible_citation():
    citation = Citation(
        evidence_id="result_1@2022", stated_value="72.4",
        claimed_geography="AZE", claimed_period="2022", claimed_value_kind="currency",
    )
    assert check_citation(citation, _EVIDENCE_INDEX) == []


def test_check_citation_accepts_a_country_name_that_resolves_to_the_right_code():
    citation = Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_geography="Azerbaijan")
    assert check_citation(citation, _EVIDENCE_INDEX) == []


def test_check_citation_rejects_an_unknown_evidence_id():
    citation = Citation(evidence_id="result_99@2022", stated_value="72.4")
    problems = check_citation(citation, _EVIDENCE_INDEX)
    assert any("does not exist" in p for p in problems)


def test_check_citation_rejects_a_value_that_does_not_match():
    citation = Citation(evidence_id="result_1@2022", stated_value="99.9")
    problems = check_citation(citation, _EVIDENCE_INDEX)
    assert any("does not match evidence_id" in p and "value" in p for p in problems)


def test_check_citation_accepts_a_reasonably_rounded_value():
    citation = Citation(evidence_id="result_1@2022", stated_value="72")
    assert check_citation(citation, _EVIDENCE_INDEX) == []


def test_check_citation_rejects_georgia_gdp_when_the_evidence_is_actually_azerbaijan():
    # The exact scenario task section 2 requires: "Georgia GDP was 72.4"
    # must fail if 72.4 belongs to Azerbaijan, even though 72.4 genuinely
    # is somewhere in the evidence package. A flat "does this number
    # exist" check could never catch this; a per-cell identity the
    # citation must declare compatible geography for, can.
    citation = Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_geography="Georgia")
    problems = check_citation(citation, _EVIDENCE_INDEX)
    assert any("geography" in p.lower() for p in problems)


def test_check_citation_rejects_a_period_mismatch():
    citation = Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_period="2023")
    problems = check_citation(citation, _EVIDENCE_INDEX)
    assert any("period" in p.lower() for p in problems)


def test_check_citation_rejects_a_value_kind_mismatch():
    # 72.4 is a currency level, not a percentage -- claiming it's a
    # percent must be caught even though the number itself matches.
    citation = Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_value_kind="percent")
    problems = check_citation(citation, _EVIDENCE_INDEX)
    assert any("value_kind" in p.lower() for p in problems)


def test_check_citation_never_penalizes_an_omitted_claim():
    # No claimed_geography/period/value_kind at all -- only the number
    # itself is checked. Not every citation needs the extra fields to pass.
    citation = Citation(evidence_id="result_1@2022", stated_value="72.4")
    assert check_citation(citation, _EVIDENCE_INDEX) == []


def test_check_citation_covers_a_derived_growth_entry_and_its_inputs():
    citation = Citation(evidence_id="result_3@2022", stated_value="3.2%", claimed_value_kind="percent")
    assert check_citation(citation, _EVIDENCE_INDEX) == []
    assert _EVIDENCE_INDEX["result_3@2022"]["input_evidence_ids"] == ["result_1@2021", "result_1@2022"]


# ---- check_answer_grounding --------------------------------------------------


def test_check_answer_grounding_accepts_a_fully_cited_answer():
    draft = AnswerDraft(
        text="Azerbaijan's GDP was 72.4 billion in 2022.",
        citations=(Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_geography="AZE"),),
    )
    result = check_answer_grounding(draft, _EVIDENCE_INDEX)
    assert result["ok"] is True
    assert result["ungrounded_numbers"] == []
    assert result["citation_problems"] == {}


def test_check_answer_grounding_flags_a_number_with_no_citation_at_all():
    draft = AnswerDraft(text="Azerbaijan's GDP was 99.9 billion in 2022.", citations=())
    result = check_answer_grounding(draft, _EVIDENCE_INDEX)
    assert result["ok"] is False
    assert 99.9 in result["ungrounded_numbers"]


def test_check_answer_grounding_flags_the_georgia_azerbaijan_mismatch_end_to_end():
    draft = AnswerDraft(
        text="Georgia's GDP was 72.4 billion in 2022.",
        citations=(Citation(evidence_id="result_1@2022", stated_value="72.4", claimed_geography="Georgia"),),
    )
    result = check_answer_grounding(draft, _EVIDENCE_INDEX)
    assert result["ok"] is False
    # The citation is incompatible, so it doesn't count toward grounding
    # 72.4 either -- the number ends up both ungrounded AND flagged.
    assert result["citation_problems"]
    assert 72.4 in result["ungrounded_numbers"]


def test_check_answer_grounding_ignores_year_references_without_a_citation():
    draft = AnswerDraft(
        text="In 2022, Azerbaijan's GDP was 72.4 billion.",
        citations=(Citation(evidence_id="result_1@2022", stated_value="72.4"),),
    )
    result = check_answer_grounding(draft, _EVIDENCE_INDEX)
    assert result["ok"] is True


# ---- write_and_verify_answer ------------------------------------------------


def test_write_and_verify_answer_succeeds_on_the_first_grounded_attempt():
    client = FakeClient(
        [
            _tool_response(
                "Azerbaijan's GDP was 72.4 billion in 2022.",
                [{"evidence_id": "result_1@2022", "stated_value": "72.4", "claimed_geography": "AZE"}],
            )
        ]
    )
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE_PACKAGE, writer, fallback_text="fallback")

    assert isinstance(result, AnswerWriteResult)
    assert result.llm_written is True
    assert result.attempts == 1
    assert "72.4" in result.text
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": WRITE_ANSWER_TOOL_NAME}


def test_write_and_verify_answer_retries_after_an_ungrounded_attempt_then_succeeds():
    client = FakeClient(
        [
            _tool_response("Azerbaijan's GDP was 99.9 billion in 2022.", []),
            _tool_response(
                "Azerbaijan's GDP was 72.4 billion in 2022.",
                [{"evidence_id": "result_1@2022", "stated_value": "72.4", "claimed_geography": "AZE"}],
            ),
        ]
    )
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE_PACKAGE, writer, fallback_text="fallback")

    assert result.llm_written is True
    assert result.attempts == 2
    assert "72.4" in result.text


def test_write_and_verify_answer_falls_back_when_the_writer_keeps_mislabeling_geography():
    client = FakeClient(
        [
            _tool_response(
                "Georgia's GDP was 72.4 billion in 2022.",
                [{"evidence_id": "result_1@2022", "stated_value": "72.4", "claimed_geography": "Georgia"}],
            ),
            _tool_response(
                "Georgia's GDP was 72.4 billion in 2022.",
                [{"evidence_id": "result_1@2022", "stated_value": "72.4", "claimed_geography": "Georgia"}],
            ),
        ]
    )
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE_PACKAGE, writer, fallback_text="fallback answer", max_attempts=2)

    assert result.llm_written is False
    assert result.text == "fallback answer"
    assert result.citation_problems
    assert result.attempts == 2


def test_write_and_verify_answer_falls_back_when_the_writer_call_raises():
    writer = AnthropicAnswerWriter(client=RaisingClient())

    result = write_and_verify_answer(_EVIDENCE_PACKAGE, writer, fallback_text="fallback answer")

    assert result.llm_written is False
    assert result.text == "fallback answer"
    assert result.ungrounded_numbers == ()


# ---- schema/value_kind consistency ------------------------------------------


def test_citation_value_kind_enum_matches_agent_evidence_value_kinds():
    from universal_statistician.agent.answer_writer import WRITE_ANSWER_TOOL_SCHEMA

    schema_kinds = set(
        WRITE_ANSWER_TOOL_SCHEMA["input_schema"]["properties"]["citations"]["items"]["properties"][
            "claimed_value_kind"
        ]["enum"]
    )
    assert schema_kinds == set(VALUE_KINDS)

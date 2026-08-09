"""Answer-writing pass + numeric consistency guard (Phase 6).

Same fake-client-from-real-anthropic-types pattern as test_agent_loop.py/
test_chat.py — no ANTHROPIC_API_KEY in this sandbox, so the AnthropicAnswerWriter
plumbing is exercised end to end against scripted Message objects, and the
number-checking logic (the part that matters most — task section 9's "never
silently return unsupported prose") is tested directly and thoroughly.
"""

from __future__ import annotations

from anthropic.types import Message, TextBlock, Usage

from universal_statistician.agent.answer_writer import (
    AnswerWriteResult,
    check_answer_numbers,
    collect_allowed_numbers,
    extract_numbers,
    write_and_verify_answer,
)
from universal_statistician.agent.llm import AnthropicAnswerWriter


def _message(text: str) -> Message:
    return Message(
        id="msg_test", model="claude-sonnet-5", role="assistant", type="message",
        stop_reason="end_turn", stop_sequence=None,
        content=[TextBlock(type="text", text=text)],
        usage=Usage(input_tokens=1, output_tokens=1),
    )


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._responses.pop(0)


class RaisingClient:
    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("network is down")


_EVIDENCE = {
    "question": "What was Azerbaijan's GDP in 2022?",
    "table": {
        "rows": [
            {"period": "2021", "gdp": 54.6},
            {"period": "2022", "gdp": 78.83},
        ],
    },
    "provenance_references": [
        {
            "value": 78.83,
            "inputs": [
                {"value": 42.6},
                {"value": 36.23},
            ],
        }
    ],
}


# ---- extract_numbers ------------------------------------------------------


def test_extract_numbers_finds_plain_and_decimal_numbers():
    assert extract_numbers("GDP was 78.83 billion, up from 54.6.") == [(78.83, 2), (54.6, 1)]


def test_extract_numbers_handles_percentages():
    assert extract_numbers("Growth was 3.2%.") == [(3.2, 1)]


def test_extract_numbers_handles_comma_thousands():
    assert extract_numbers("Population was 10,400,000.") == [(10400000.0, 0)]


def test_extract_numbers_handles_negative_numbers():
    assert extract_numbers("Growth was -3.2% year over year.") == [(-3.2, 1)]


def test_extract_numbers_excludes_bare_four_digit_years():
    numbers = extract_numbers("In 2022, GDP was 78.83 billion, compared with 2021.")
    assert (78.83, 2) in numbers
    assert not any(value == 2022 for value, _ in numbers)
    assert not any(value == 2021 for value, _ in numbers)


def test_extract_numbers_does_not_exclude_a_four_digit_number_with_decimals():
    numbers = extract_numbers("The index reached 2022.50 this year.")
    assert (2022.50, 2) in numbers


# ---- collect_allowed_numbers -----------------------------------------------


def test_collect_allowed_numbers_reads_table_rows_and_provenance():
    allowed = collect_allowed_numbers(_EVIDENCE)
    assert 54.6 in allowed
    assert 78.83 in allowed
    assert 42.6 in allowed
    assert 36.23 in allowed
    assert "period" not in allowed


def test_collect_allowed_numbers_handles_missing_sections_gracefully():
    assert collect_allowed_numbers({}) == []


# ---- check_answer_numbers ---------------------------------------------------


def test_check_answer_numbers_accepts_a_correctly_rounded_number():
    check = check_answer_numbers("Azerbaijan's GDP reached 78.8 billion in 2022.", _EVIDENCE)
    assert check["ok"] is True
    assert check["unsupported_numbers"] == []


def test_check_answer_numbers_accepts_an_exact_number():
    check = check_answer_numbers("GDP was 78.83 billion, up from 54.6.", _EVIDENCE)
    assert check["ok"] is True


def test_check_answer_numbers_rejects_a_fabricated_number():
    check = check_answer_numbers("Azerbaijan's GDP reached 99.9 billion in 2022.", _EVIDENCE)
    assert check["ok"] is False
    assert 99.9 in check["unsupported_numbers"]


def test_check_answer_numbers_ignores_year_references():
    check = check_answer_numbers("In 2022, GDP was 78.83 billion.", _EVIDENCE)
    assert check["ok"] is True


# ---- write_and_verify_answer ------------------------------------------------


def test_write_and_verify_answer_succeeds_on_the_first_supported_attempt():
    client = FakeClient([_message("Azerbaijan's GDP reached 78.8 billion in 2022, up from 54.6 billion in 2021.")])
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE, writer, fallback_text="fallback")

    assert isinstance(result, AnswerWriteResult)
    assert result.llm_written is True
    assert result.attempts == 1
    assert "78.8" in result.text


def test_write_and_verify_answer_retries_after_an_unsupported_number_then_succeeds():
    client = FakeClient(
        [
            _message("Azerbaijan's GDP reached 99.9 billion in 2022."),
            _message("Azerbaijan's GDP reached 78.83 billion in 2022."),
        ]
    )
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE, writer, fallback_text="fallback")

    assert result.llm_written is True
    assert result.attempts == 2
    assert "78.83" in result.text


def test_write_and_verify_answer_falls_back_when_every_attempt_is_unsupported():
    client = FakeClient(
        [
            _message("GDP reached 99.9 billion."),
            _message("GDP reached 12.3 billion."),
        ]
    )
    writer = AnthropicAnswerWriter(client=client)

    result = write_and_verify_answer(_EVIDENCE, writer, fallback_text="fallback answer", max_attempts=2)

    assert result.llm_written is False
    assert result.text == "fallback answer"
    assert result.unsupported_numbers == (12.3,)
    assert result.attempts == 2


def test_write_and_verify_answer_falls_back_when_the_writer_call_raises():
    writer = AnthropicAnswerWriter(client=RaisingClient())

    result = write_and_verify_answer(_EVIDENCE, writer, fallback_text="fallback answer")

    assert result.llm_written is False
    assert result.text == "fallback answer"
    assert result.unsupported_numbers == ()

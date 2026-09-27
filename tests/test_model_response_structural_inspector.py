"""Offline privacy and adapter-gap evidence for LiteLLM response shapes."""

import json

import pytest
from litellm import ModelResponse

from tau2.data_model.message import UserMessage
from tau2.harness.model_response_inspector import (
    inspect_model_response_structure,
    summarize_assistant_output_structure,
)
from tau2.harness.retry import ProviderGenerationRetryController, RetryBudgetExhausted
from tau2.utils import llm_utils


def _response(message: dict, *, completion_tokens: int = 1) -> ModelResponse:
    return ModelResponse(
        id="structural-test",
        model="mock-model",
        choices=[{"index": 0, "finish_reason": "stop", "message": message}],
        usage={"prompt_tokens": 2, "completion_tokens": completion_tokens, "total_tokens": 2 + completion_tokens},
    )


@pytest.mark.parametrize(
    "message,expected_path",
    [
        ({"role": "assistant", "content": "private text"}, "response.choices[0].message.content"),
        ({"role": "assistant", "content": None, "tool_calls": [{"id": "x", "function": {"name": "lookup", "arguments": "{secret}"}}]}, "response.choices[0].message.tool_calls"),
        ({"role": "assistant", "content": None, "reasoning_content": "private reasoning"}, "response.choices[0].message.reasoning_content"),
        ({"role": "assistant", "content": None, "provider_specific_fields": {"thinking": "private thinking"}}, "response.choices[0].message.provider_specific_fields.thinking"),
    ],
)
def test_inspector_reports_shape_without_values(message, expected_path):
    rows = inspect_model_response_structure(_response(message))
    paths = {row["path"] for row in rows}
    assert expected_path in paths
    serialized = json.dumps(rows, sort_keys=True)
    for forbidden in ("private text", "private reasoning", "private thinking", "{secret}"):
        assert forbidden not in serialized


def test_usage_can_report_output_tokens_without_exposed_assistant_output():
    rows = inspect_model_response_structure(
        _response({"role": "assistant", "content": None, "tool_calls": []}, completion_tokens=167)
    )
    by_path = {row["path"]: row for row in rows}
    assert by_path["response.choices[0].message.content"]["null"] is True
    # This LiteLLM version normalizes an empty list to None in Message.__init__.
    assert by_path["response.choices[0].message.tool_calls"]["null"] is True
    # Scalar token values are intentionally not exposed by the inspector.
    assert "value" not in by_path["response.usage.completion_tokens"]


def test_adapter_ignores_reasoning_content_and_classifies_empty(monkeypatch):
    response = _response({"role": "assistant", "content": None, "reasoning_content": "not visible to tau2"}, completion_tokens=9)
    calls = []
    monkeypatch.setattr(llm_utils, "completion", lambda **_: (calls.append(1), response)[1])
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate(
            "mock",
            [UserMessage(role="user", content="safe")],
            provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
        )
    assert len(calls) == 3
    rows = inspect_model_response_structure(response)
    reasoning = next(row for row in rows if row["path"] == "response.choices[0].message.reasoning_content")
    assert reasoning["string_length"] > 0


def test_inspector_is_deterministic_and_does_not_mutate_response():
    response = _response({"role": "assistant", "content": None, "refusal": "private refusal", "provider_specific_fields": {"thinking": "secret"}})
    before = response.model_dump()
    first = inspect_model_response_structure(response)
    second = inspect_model_response_structure(response)
    assert first == second
    assert response.model_dump() == before
    serialized = json.dumps(first, sort_keys=True)
    assert "private refusal" not in serialized and "secret" not in serialized


def test_compact_summary_detects_alternate_output_without_exposing_it():
    response = _response({
        "role": "assistant",
        "content": None,
        "reasoning_content": "private reasoning",
        "thinking_blocks": [{"type": "thinking", "thinking": "private thought", "signature": "private signature"}],
        "provider_specific_fields": {"thinking": "private provider output"},
        "refusal": "private refusal",
    }, completion_tokens=167)
    summary = summarize_assistant_output_structure(response)
    assert summary["content_present"] is False
    assert summary["tool_calls_present"] is False
    assert summary["reasoning_content"]["present"] is True
    assert summary["reasoning_content"]["length_or_count"] > 0
    assert summary["thinking_blocks_present"] is True
    assert summary["provider_specific_fields_child_count"] == 1
    assert summary["refusal"]["present"] is True
    assert summary["output_tokens"] == 167
    serialized = json.dumps(summary, sort_keys=True)
    for forbidden in ("private reasoning", "private thought", "private signature", "private provider output", "private refusal"):
        assert forbidden not in serialized

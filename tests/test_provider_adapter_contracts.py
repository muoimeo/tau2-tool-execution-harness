"""Offline response-shape contracts for the LiteLLM-to-tau2 adapter."""

from types import SimpleNamespace

import json
import pytest

from tau2.data_model.message import UserMessage
from tau2.harness.retry import (
    ProviderGenerationRetryController,
    ProviderRetrySidecar,
    RetryBudgetExhausted,
)
from tau2.utils import llm_utils


def _object_response(*, content=None, tool_calls=None, finish_reason="stop", message=True):
    msg = (
        SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)
        if message
        else None
    )
    choice = SimpleNamespace(finish_reason=finish_reason, message=msg)
    choice.to_dict = lambda: {"finish_reason": finish_reason}
    return SimpleNamespace(
        choices=[choice], id="completion-object", _request_id="transport-object",
        model="resolved-object", usage=None,
    )


def _dict_response(*, content=None, tool_calls=None, finish_reason="stop", message=True):
    return {
        "choices": ([{"finish_reason": finish_reason, "message": {
            "role": "assistant", "content": content, "tool_calls": tool_calls,
        }}] if message else [{"finish_reason": finish_reason, "message": None}]),
        "id": "completion-dict", "_request_id": "transport-dict",
        "model": "resolved-dict", "usage": None,
    }


def _patch_cost(monkeypatch):
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)


@pytest.mark.parametrize(
    "factory,finish_reason,content",
    [
        (_object_response, "stop", "object text"),
        (_object_response, "length", "partial object text"),
        (_dict_response, "stop", "dict text"),
        (_dict_response, "length", "partial dict text"),
    ],
)
def test_adapter_preserves_valid_content_for_object_and_dict_shapes(monkeypatch, factory, finish_reason, content):
    _patch_cost(monkeypatch)
    monkeypatch.setattr(llm_utils, "completion", lambda **_: factory(content=content, finish_reason=finish_reason))
    message = llm_utils.generate("mock", [UserMessage(role="user", content="safe")])
    assert message.content == content
    assert message.tool_calls is None


@pytest.mark.parametrize("factory", [_object_response, _dict_response])
def test_adapter_preserves_valid_tool_calls_for_object_and_dict_shapes(monkeypatch, factory):
    _patch_cost(monkeypatch)
    if factory is _object_response:
        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="lookup", arguments='{"id":"42"}'),
        )
    else:
        call = {"id": "call-1", "function": {"name": "lookup", "arguments": '{"id":"42"}'}}
    monkeypatch.setattr(llm_utils, "completion", lambda **_: factory(tool_calls=[call], finish_reason="tool_calls"))
    message = llm_utils.generate("mock", [UserMessage(role="user", content="safe")])
    assert message.content is None
    assert [(call.id, call.name, call.arguments) for call in message.tool_calls] == [
        ("call-1", "lookup", {"id": "42"})
    ]


@pytest.mark.parametrize(
    "response_value",
    [
        SimpleNamespace(choices=[], id="empty-choices", model="mock", usage=None),
        {"choices": [], "id": "empty-choices-dict", "model": "mock"},
        _object_response(message=False),
        _dict_response(message=False),
        _object_response(content=None, tool_calls=[]),
        _dict_response(content=None, tool_calls=[]),
        _object_response(content="", tool_calls=[]),
        _dict_response(content="", tool_calls=[]),
    ],
)
def test_empty_or_missing_assistant_shapes_never_materialize_an_assistant_message(monkeypatch, response_value):
    calls = []
    monkeypatch.setattr(llm_utils, "completion", lambda **_: (calls.append(1), response_value)[1])
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate(
            "mock", [UserMessage(role="user", content="safe")],
            provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
        )
    assert len(calls) == 3


def test_sidecar_v3_preserves_safe_shape_and_id_provenance_without_raw_payload(tmp_path, monkeypatch):
    _patch_cost(monkeypatch)
    secret = "do-not-persist-this"
    response = _object_response(content=None, tool_calls=[])
    response.usage = SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15)
    monkeypatch.setattr(llm_utils, "completion", lambda **_: response)
    sidecar = ProviderRetrySidecar(tmp_path / "probe.provider-retry.jsonl", generation_id="g1", turn_index=1, episode_attempt=1)
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate(
            "mock", [UserMessage(role="user", content=secret)],
            provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None, observer=sidecar.record),
        )
    rows = [json.loads(line) for line in sidecar.path.read_text(encoding="utf-8").splitlines()]
    assert all(row["schema_version"] == "provider-observability/v1" for row in rows)
    assert all(row["response_type"] == "SimpleNamespace" for row in rows)
    assert all(row["choices_count"] == 1 and row["message_present"] is True for row in rows)
    assert all(row["tool_call_count"] == 0 and row["completion_id"] == "completion-object" for row in rows)
    assert all(row["transport_request_id"] == "transport-object" and row["id_source"] == "transport_request_id" for row in rows)
    raw = sidecar.path.read_text(encoding="utf-8")
    assert secret not in raw and "raw_tool_calls" not in raw and '"content":' not in raw


def test_a2_empty_response_records_only_safe_structural_metadata(tmp_path, monkeypatch):
    _patch_cost(monkeypatch)
    secret = "do-not-persist-reasoning"
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(
                role="assistant", content=None, tool_calls=[],
                reasoning_content=secret, refusal="hidden-refusal",
                provider_specific_fields={"thinking": "hidden-thinking"},
            ),
        )],
        id="completion-structural", model="resolved-model",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=167, total_tokens=179),
    )
    monkeypatch.setattr(llm_utils, "completion", lambda **_: response)
    sidecar = ProviderRetrySidecar(tmp_path / "provider.jsonl", generation_id="g1", turn_index=1, episode_attempt=1)
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate(
            "mock", [UserMessage(role="user", content="safe")],
            provider_retry_controller=ProviderGenerationRetryController(
                sleeper=lambda _: None, observer=sidecar.record
            ),
        )
    rows = [json.loads(line) for line in sidecar.path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    structure = rows[0]["empty_response_structure"]
    assert structure["reasoning_content"] == {"present": True, "length_or_count": len(secret)}
    assert structure["refusal"]["present"] is True
    assert structure["provider_specific_fields_child_count"] == 1
    assert structure["output_tokens"] == 167
    persisted = sidecar.path.read_text(encoding="utf-8")
    assert secret not in persisted and "hidden-refusal" not in persisted and "hidden-thinking" not in persisted


def test_structural_inspector_runs_only_for_empty_and_failure_keeps_retry_semantics(monkeypatch):
    _patch_cost(monkeypatch)
    observed = []
    monkeypatch.setattr(llm_utils, "summarize_assistant_output_structure", lambda value: observed.append(value) or {})
    normal = _object_response(content="normal")
    monkeypatch.setattr(llm_utils, "completion", lambda **_: normal)
    result = llm_utils.generate(
        "mock", [UserMessage(role="user", content="safe")],
        provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
    )
    assert result.content == "normal" and observed == []

    empty = _object_response(content=None, tool_calls=[])
    calls = []
    monkeypatch.setattr(llm_utils, "completion", lambda **_: (calls.append(1), empty)[1])
    monkeypatch.setattr(
        llm_utils, "summarize_assistant_output_structure",
        lambda _value: (_ for _ in ()).throw(RuntimeError("inspection failure")),
    )
    with pytest.raises(RetryBudgetExhausted) as exc_info:
        llm_utils.generate(
            "mock", [UserMessage(role="user", content="safe")],
            provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
        )
    assert exc_info.value.last_error.kind.value == "PROVIDER_EMPTY_ASSISTANT_MESSAGE"
    assert len(calls) == 3


def test_provider_only_diagnostic_skips_cost_and_raw_response_retention(monkeypatch):
    response = _object_response(content="safe")
    response.to_dict = lambda: (_ for _ in ()).throw(AssertionError("raw response must not serialize"))
    _patch_cost(monkeypatch)
    monkeypatch.setattr(llm_utils, "completion", lambda **_: response)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: (_ for _ in ()).throw(AssertionError("cost must not calculate")))
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: (_ for _ in ()).throw(AssertionError("usage must not materialize")))
    message = llm_utils.generate(
        "mock", [UserMessage(role="user", content="safe")],
        _diagnostic_skip_cost=True, _diagnostic_no_raw_data=True, _diagnostic_skip_usage=True,
    )
    assert message.cost == 0.0 and message.raw_data is None and message.content == "safe"

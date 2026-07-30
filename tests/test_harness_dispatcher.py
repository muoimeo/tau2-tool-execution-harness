"""HAR-05 safe dispatcher tests: no runtime integration and no retry."""

from datetime import datetime, timezone

import pytest

from tau2.harness.contracts import (
    HarnessErrorCode,
    HarnessStage,
    Requestor,
    ToolCallRequest,
    ValidationDecision,
    ValidationResult,
    ValidationViolation,
    canonical_json_bytes,
    canonical_json_sha256,
)
from tau2.harness.dispatcher import A1ValidationExecutor, DuplicateExecutionError, NativeToolExecutor
from tau2.harness.parser import ParserNormalizer, RawToolCall
from tau2.harness.registry import RegistryTool


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def resolved_tool(name: str = "tool") -> RegistryTool:
    schema = {"type": "object"}
    return RegistryTool(
        tool_name=name, allowed_requestor=Requestor.ASSISTANT,
        toolkit_identity="tests:Toolkit", params_model_identity="tests:Params",
        params_schema_json=canonical_json_bytes(schema).decode("utf-8"),
        params_schema_sha256=canonical_json_sha256(schema), native_tool_type="write",
    )


def request(name: str = "tool", arguments: dict | None = None, logical_call_id: str = "provider-1"):
    return ToolCallRequest.parsed(
        run_id="har05-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=7, logical_call_id=logical_call_id, requestor=Requestor.ASSISTANT,
        tool_name=name, arguments={} if arguments is None else arguments,
        provider_call_id=logical_call_id, raw_arguments_text="{}", created_at=NOW,
    )


def reject_validation(call: ToolCallRequest, code: HarnessErrorCode, stage: HarnessStage) -> ValidationResult:
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.REJECT, failed_stage=stage, failure_code=code,
        violations=(ValidationViolation(code=code, path="$", message="rejected"),),
        validated_at=NOW,
    )


def allow_validation(call: ToolCallRequest) -> ValidationResult:
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.ALLOW, passed_stages=(HarnessStage.SCHEMA, HarnessStage.DOMAIN_CONSTRAINT),
        validated_at=NOW,
    )


@pytest.mark.parametrize("code,stage", [
    (HarnessErrorCode.SCHEMA_MISSING_REQUIRED_ARG, HarnessStage.SCHEMA),
    (HarnessErrorCode.REGISTRY_UNKNOWN_TOOL, HarnessStage.REGISTRY),
    (HarnessErrorCode.REGISTRY_WRONG_REQUESTOR, HarnessStage.REQUESTOR),
    (HarnessErrorCode.DOMAIN_CONSTRAINT_VIOLATION, HarnessStage.DOMAIN_CONSTRAINT),
])
def test_validation_rejects_never_invoke_and_preserve_original_id_and_state(code, stage):
    state = {"writes": 0}
    calls = 0

    def write_tool(**kwargs):
        nonlocal calls
        calls += 1
        state["writes"] += 1
        return "should not run"

    call = request(arguments={"value": "x"})
    result = A1ValidationExecutor(NativeToolExecutor(write_tool, state_supplier=lambda: state)).materialize(
        call, reject_validation(call, code, stage), resolved_tool(), completed_at=NOW
    )
    assert calls == 0
    assert result.dispatch_attempted is False and result.dispatch_count == 0
    assert result.attempt_index == 0
    assert result.tool_message.id == call.logical_call_id
    assert result.tool_message.role == "tool" and result.tool_message.requestor == Requestor.ASSISTANT
    assert result.tool_message.error is True
    assert result.tool_message.content == f"Tool call rejected: {code.value}."
    assert result.state_before.sha256 == result.state_after.sha256


def test_parser_failure_materialization_preserves_synthetic_id_without_invocation():
    calls = 0

    def native_tool(**kwargs):
        nonlocal calls
        calls += 1

    from tau2.harness.contracts import TrustedRouteContext
    parsed = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="bad-1", raw_tool_name="tool", raw_arguments_text="{"),),
        run_id="har05-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=7, trusted_route=TrustedRouteContext(route_id="agent", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    )
    failure = parsed.failures[0]
    result = A1ValidationExecutor(NativeToolExecutor(native_tool)).materialize_parser_failure(failure, completed_at=NOW)
    assert calls == 0
    assert result.logical_call_id == failure.synthetic_call_id == result.tool_message.id
    assert result.harness_error.code == HarnessErrorCode.PARSE_MALFORMED_JSON
    assert result.dispatch_count == 0 and result.tool_message.error is True


def test_allowed_read_and_write_execute_exactly_once_and_arguments_remain_unchanged():
    read_calls, write_calls = 0, 0
    state = {"writes": 0}

    def read_tool(query):
        nonlocal read_calls
        read_calls += 1
        return {"query": query}

    def write_tool(value):
        nonlocal write_calls
        write_calls += 1
        state["writes"] += value
        return "written"

    read_call = request(arguments={"query": "status"}, logical_call_id="read-1")
    read_before = read_call.arguments_json
    read_result = A1ValidationExecutor(NativeToolExecutor(read_tool)).materialize(
        read_call, allow_validation(read_call), resolved_tool(), completed_at=NOW
    )
    write_call = request(arguments={"value": 2}, logical_call_id="write-1")
    write_before = write_call.arguments_json
    write_result = A1ValidationExecutor(NativeToolExecutor(write_tool, state_supplier=lambda: state)).materialize(
        write_call, allow_validation(write_call), resolved_tool(), completed_at=NOW
    )
    assert read_calls == 1 and write_calls == 1
    assert read_result.dispatch_count == write_result.dispatch_count == 1
    assert read_result.tool_message.error is False and write_result.tool_message.error is False
    assert read_call.arguments_json == read_before and write_call.arguments_json == write_before
    assert write_result.state_before.sha256 != write_result.state_after.sha256


@pytest.mark.parametrize("exception", [ValueError("customer a@example.com #W1234567"), Exception("generic failure")])
def test_native_exceptions_are_contained_once_with_safe_visible_content(exception):
    calls = 0

    def broken_tool(**kwargs):
        nonlocal calls
        calls += 1
        raise exception

    call = request(logical_call_id=f"broken-{type(exception).__name__}")
    result = A1ValidationExecutor(NativeToolExecutor(broken_tool)).materialize(
        call, allow_validation(call), resolved_tool(), completed_at=NOW
    )
    assert calls == 1
    assert result.status.value == "native_error" and result.dispatch_count == 1
    assert result.harness_error.code == HarnessErrorCode.UNKNOWN_EXECUTION_ERROR
    assert result.harness_error.cause_type == type(exception).__name__
    assert result.tool_message.content == "Tool execution failed."
    assert "a@example.com" not in (result.harness_error.cause_message_redacted or "")
    assert "#W1234567" not in (result.harness_error.cause_message_redacted or "")


def test_duplicate_execution_guard_rejects_second_materialization_without_second_call():
    calls = 0

    def native_tool(**kwargs):
        nonlocal calls
        calls += 1
        return "ok"

    call = request(logical_call_id="once")
    executor = A1ValidationExecutor(NativeToolExecutor(native_tool))
    first = executor.materialize(call, allow_validation(call), resolved_tool(), completed_at=NOW)
    assert first.dispatch_count == 1 and calls == 1
    with pytest.raises(DuplicateExecutionError, match="EXECUTION_DUPLICATE_DISPATCH"):
        executor.materialize(call, allow_validation(call), resolved_tool(), completed_at=NOW)
    assert calls == 1


def test_same_provider_id_in_different_trial_is_not_false_duplicate():
    calls = 0

    def native_tool(**kwargs):
        nonlocal calls
        calls += 1
        return "ok"

    first = request(logical_call_id="reused-id")
    second = ToolCallRequest.parsed(
        run_id=first.run_id, task_id=first.task_id, trial_index=1, trial_seed=373753,
        turn_index=first.turn_index, logical_call_id="reused-id", requestor=first.requestor,
        tool_name="tool", arguments={}, provider_call_id="reused-id", raw_arguments_text="{}", created_at=NOW,
    )
    executor = A1ValidationExecutor(NativeToolExecutor(native_tool))
    executor.materialize(first, allow_validation(first), resolved_tool(), completed_at=NOW)
    executor.materialize(second, allow_validation(second), resolved_tool(), completed_at=NOW)
    assert calls == 2

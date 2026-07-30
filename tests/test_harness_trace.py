"""HAR-06 deterministic append-only trace tests without runtime integration."""

import json
from datetime import datetime, timezone

import pytest

from tau2.harness.contracts import (
    HarnessErrorCode, HarnessStage, Requestor, ToolCallRequest,
    ValidationDecision, ValidationResult, ValidationViolation,
    canonical_json_bytes, canonical_json_sha256,
)
from tau2.harness.dispatcher import A1ValidationExecutor, NativeToolExecutor
from tau2.harness.parser import ParserNormalizer, RawToolCall
from tau2.harness.registry import RegistryTool
from tau2.harness.trace import TraceInvariantError, TraceLogger, TraceLoggerError


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def tool() -> RegistryTool:
    schema = {"type": "object"}
    return RegistryTool(
        tool_name="tool", allowed_requestor=Requestor.ASSISTANT,
        toolkit_identity="tests:Toolkit", params_model_identity="tests:Params",
        params_schema_json=canonical_json_bytes(schema).decode("utf-8"),
        params_schema_sha256=canonical_json_sha256(schema), native_tool_type="write",
    )


def request(logical_call_id="call-1", raw="{}"):
    return ToolCallRequest.parsed(
        run_id="trace-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=8, logical_call_id=logical_call_id, requestor=Requestor.ASSISTANT,
        tool_name="tool", arguments={"value": 1}, provider_call_id=logical_call_id,
        raw_arguments_text=raw, created_at=NOW,
    )


def allow(call):
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.ALLOW,
        passed_stages=(HarnessStage.SCHEMA, HarnessStage.DOMAIN_CONSTRAINT), validated_at=NOW,
    )


def reject(call):
    code = HarnessErrorCode.SCHEMA_MISSING_REQUIRED_ARG
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.REJECT, failed_stage=HarnessStage.SCHEMA,
        failure_code=code, violations=(ValidationViolation(code=code, path="value", message="missing"),),
        validated_at=NOW,
    )


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_reject_trace_skips_dispatch_phases_and_contains_full_context(tmp_path):
    path = tmp_path / "reject.jsonl"
    call, registry = request(), tool()
    dispatch = A1ValidationExecutor(NativeToolExecutor(lambda **_: "never")).materialize(
        call, reject(call), registry, completed_at=NOW
    )
    logger = TraceLogger(path)
    logger.proposed(call, tool=registry)
    logger.parsed(call, tool=registry)
    logger.validated(call, reject(call), tool=registry)
    logger.result_emitted(call, reject(call), dispatch, tool=registry)
    events = read_events(path)
    assert [event["phase"] for event in events] == ["proposed", "parsed", "validated", "result_emitted"]
    assert all(event["phase"] not in {"dispatch_started", "dispatch_completed"} for event in events)
    result = events[-1]
    assert result["run_id"] == "trace-r0" and result["task_id"] == "71"
    assert result["trial_index"] == 0 and result["turn_index"] == 8
    assert result["dispatch_count"] == 0 and result["attempt_index"] == 0
    assert result["state_hash_before"] is None and result["state_hash_after"] is None


def test_allow_trace_order_state_hash_and_deterministic_jsonl(tmp_path):
    def emit(path):
        state = {"n": 0}
        call, registry = request(), tool()
        validation = allow(call)
        dispatch = A1ValidationExecutor(NativeToolExecutor(
            lambda value: state.update(n=state["n"] + value) or "ok", state_supplier=lambda: state
        )).materialize(call, validation, registry, completed_at=NOW)
        logger = TraceLogger(path)
        logger.proposed(call, tool=registry)
        logger.parsed(call, tool=registry)
        logger.validated(call, validation, tool=registry)
        logger.dispatch_started(call, validation, tool=registry)
        logger.dispatch_completed(call, validation, dispatch, tool=registry)
        logger.result_emitted(call, validation, dispatch, tool=registry)
        return path.read_bytes()

    first, second = emit(tmp_path / "one.jsonl"), emit(tmp_path / "two.jsonl")
    assert first == second
    events = [json.loads(line) for line in first.decode("utf-8").splitlines()]
    assert [event["phase"] for event in events] == ["proposed", "parsed", "validated", "dispatch_started", "dispatch_completed", "result_emitted"]
    assert events[-1]["dispatch_count"] == 1
    assert events[-1]["state_hash_before"] != events[-1]["state_hash_after"]


def test_parser_rejection_trace_preserves_synthetic_id_without_dispatch(tmp_path):
    from tau2.harness.contracts import TrustedRouteContext
    parsed = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="bad", raw_tool_name="tool", raw_arguments_text="{"),),
        run_id="trace-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=8, trusted_route=TrustedRouteContext(route_id="agent", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    )
    failure = parsed.failures[0]
    dispatch = A1ValidationExecutor(NativeToolExecutor(lambda **_: "never")).materialize_parser_failure(failure, completed_at=NOW)
    events = TraceLogger(tmp_path / "parser.jsonl").parser_rejection(failure, dispatch)
    assert [event.phase.value for event in events] == ["proposed", "parsed", "result_emitted"]
    assert all(event.logical_call_id == failure.synthetic_call_id for event in events)
    assert events[-1].dispatch_count == 0


def test_ordering_missing_phase_and_duplicate_event_fail_closed(tmp_path):
    logger, call, registry = TraceLogger(tmp_path / "bad.jsonl"), request(), tool()
    with pytest.raises(TraceInvariantError, match="invalid trace transition"):
        logger.parsed(call, tool=registry)
    logger.proposed(call, tool=registry)
    with pytest.raises(TraceInvariantError, match="duplicate trace phase"):
        logger.proposed(call, tool=registry)


def test_reopen_reconstructs_phase_history_and_sequence(tmp_path):
    path, call, registry = tmp_path / "resume.jsonl", request(), tool()
    TraceLogger(path).proposed(call, tool=registry)
    resumed = TraceLogger(path)
    parsed = resumed.parsed(call, tool=registry)
    assert parsed.sequence == 1
    with pytest.raises(TraceInvariantError, match="duplicate trace phase"):
        resumed.proposed(call, tool=registry)


def test_default_redacts_raw_and_exception_metadata_fixture_mode_is_explicit(tmp_path):
    pii_raw = '{"email":"a@example.com","order":"#W1234567"}'
    call, registry = request(raw=pii_raw), tool()
    default_path = tmp_path / "default.jsonl"
    TraceLogger(default_path).proposed(call, tool=registry)
    assert "a@example.com" not in default_path.read_text(encoding="utf-8")
    fixture_path = tmp_path / "fixture.jsonl"
    TraceLogger(fixture_path, fixture_raw_retention=True).proposed(call, tool=registry)
    assert "a@example.com" in fixture_path.read_text(encoding="utf-8")


def test_logger_write_failure_raises_without_mutating_trace_state(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    logger = TraceLogger(blocker / "events.jsonl")
    with pytest.raises(TraceLoggerError, match="Cannot append trace JSONL"):
        logger.proposed(request(), tool=tool())
    # Retry is intentionally caller-owned; the failed write did not consume sequence 0.
    assert logger._next_sequence == 0

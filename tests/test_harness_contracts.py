"""HAR-01 contract invariants: pure model tests, no LLM/gateway integration."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tau2.domains.retail.environment import get_environment
from tau2.harness.contracts import (
    DispatchResult,
    DispatchStatus,
    HarnessError,
    HarnessErrorCode,
    HarnessStage,
    MaterializationOrigin,
    ParseStatus,
    RequestSourceKind,
    Requestor,
    SchemaValidationPolicy,
    StateHashSnapshot,
    ToolCallRequest,
    ToolEvent,
    ToolEventPhase,
    ToolMessageRecord,
    ValidationDecision,
    ValidationResult,
    ValidationViolation,
    canonical_json_bytes,
    synthetic_parser_rejection_id,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
REQUEST_SHA = "a" * 64


def parsed_request() -> ToolCallRequest:
    return ToolCallRequest.parsed(
        run_id="a1-r0",
        task_id="71",
        trial_index=0,
        trial_seed=626729,
        turn_index=2,
        logical_call_id="provider-01",
        requestor=Requestor.ASSISTANT,
        tool_name="get_order_details",
        arguments={"order_id": "#W0000000"},
        provider_call_id="provider-01",
        raw_arguments_text='{"order_id":"#W0000000"}',
        created_at=NOW,
    )


def parser_rejection(ordinal: int = 0) -> ToolCallRequest:
    return ToolCallRequest.parser_rejection(
        run_id="a1-r0",
        task_id="71",
        trial_index=0,
        trial_seed=626729,
        turn_index=2,
        malformed_call_ordinal=ordinal,
        requestor=Requestor.ASSISTANT,
        error_code=HarnessErrorCode.PARSE_MALFORMED_JSON,
        provider_call_id=None,
        raw_tool_name="get_order_details",
        raw_arguments_text='{"order_id":',
        created_at=NOW,
    )


def rejection_error(call: ToolCallRequest) -> HarnessError:
    return HarnessError(
        error_id="herr-01",
        logical_call_id=call.logical_call_id,
        stage=HarnessStage.PARSE,
        code=HarnessErrorCode.PARSE_MALFORMED_JSON,
        message="Malformed tool arguments.",
        details_json="{}",
        created_at=NOW,
    )


def test_canonical_json_sorts_object_keys_but_preserves_list_order():
    assert canonical_json_bytes({"b": 1, "a": [2, 1]}) == b'{"a":[2,1],"b":1}'
    assert canonical_json_bytes([1, 2]) != canonical_json_bytes([2, 1])
    assert StateHashSnapshot.from_full_db_state({"b": 1, "a": [2, 1]}) == StateHashSnapshot.from_full_db_state({"a": [2, 1], "b": 1})
    with pytest.raises(ValidationError):
        StateHashSnapshot(sha256="b" * 64, volatile_paths=("updated_at",))


def test_parser_rejection_identity_requires_requestor_and_ordinal():
    first = parser_rejection(0)
    duplicate_in_same_turn = parser_rejection(1)
    assert first.logical_call_id != duplicate_in_same_turn.logical_call_id
    assert first.logical_call_id == synthetic_parser_rejection_id(
        run_id="a1-r0",
        turn_index=2,
        malformed_call_ordinal=0,
        provider_call_id=None,
        requestor=Requestor.ASSISTANT,
        raw_tool_name="get_order_details",
        raw_arguments_text='{"order_id":',
    )
    with pytest.raises(ValidationError, match="malformed_call_ordinal"):
        ToolCallRequest(
            run_id="a1-r0", task_id="71", trial_index=0, trial_seed=626729,
            turn_index=2, logical_call_id="hparse_notcanonical",
            source_kind=RequestSourceKind.SYNTHETIC_PARSER_REJECTION,
            requestor=Requestor.ASSISTANT, parse_status=ParseStatus.REJECTED,
            parse_error_code=HarnessErrorCode.PARSE_MALFORMED_JSON, created_at=NOW,
        )
    with pytest.raises(ValidationError):
        ToolEvent(
            event_id="event-01", run_id="a1-r0", logical_call_id=first.logical_call_id,
            sequence=0, phase=ToolEventPhase.PROPOSED, request_sha256=first.request_sha256,
            timestamp=NOW,
        )


def test_request_validation_error_and_dispatch_invariants_are_model_enforced():
    call = parsed_request()
    with pytest.raises(ValidationError, match="allow result cannot carry failure metadata"):
        ValidationResult(
            logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
            decision=ValidationDecision.ALLOW, failed_stage=HarnessStage.SCHEMA,
            validated_at=NOW,
        )
    rejected = ValidationResult(
        logical_call_id=call.logical_call_id,
        request_sha256=call.request_sha256,
        decision=ValidationDecision.REJECT,
        failed_stage=HarnessStage.SCHEMA,
        failure_code=HarnessErrorCode.SCHEMA_EXTRA_ARG,
        violations=(ValidationViolation(
            code=HarnessErrorCode.SCHEMA_EXTRA_ARG, path="extra", message="unexpected"
        ),),
        validated_at=NOW,
    )
    error_call = parser_rejection()
    with pytest.raises(ValidationError, match="dispatch_count=0"):
        DispatchResult(
            logical_call_id=error_call.logical_call_id,
            request_sha256=error_call.request_sha256,
            validation_decision=ValidationDecision.REJECT,
            dispatch_attempted=True,
            dispatch_count=1,
            status=DispatchStatus.NOT_DISPATCHED,
            origin=MaterializationOrigin.HARNESS_REJECT,
            tool_message=ToolMessageRecord(
                id=error_call.logical_call_id, requestor=Requestor.ASSISTANT,
                content="error", error=True,
            ),
            harness_error=rejection_error(error_call),
            completed_at=NOW,
        )
    assert rejected.arguments_unchanged is True


def test_harness_error_and_event_are_non_retry_contracts():
    call = parser_rejection()
    with pytest.raises(ValidationError):
        HarnessError(
            error_id="herr-01", logical_call_id=call.logical_call_id,
            stage=HarnessStage.PARSE, code=HarnessErrorCode.PARSE_MALFORMED_JSON,
            message="Malformed", details_json="{}", retryable=True, created_at=NOW,
        )
    with pytest.raises(ValidationError, match="dispatch events require allow decision"):
        ToolEvent(
            event_id="event-02", run_id="a1-r0", task_id="71", trial_index=0,
            trial_seed=626729, turn_index=2, logical_call_id=call.logical_call_id,
            sequence=1, phase=ToolEventPhase.DISPATCH_STARTED,
            requestor=Requestor.ASSISTANT, decision=ValidationDecision.REJECT,
            request_sha256=call.request_sha256, timestamp=NOW,
        )


def test_per_call_strict_schema_does_not_mutate_native_tool_model():
    """Proves the HAR-04 call boundary without dispatching any tool."""
    tool = next(t for t in get_environment().get_tools() if t.name == "get_order_details")
    before = dict(tool.params.model_config)
    with pytest.raises(ValidationError):
        tool.params.model_validate(
            {"order_id": "#W0000000", "unexpected": 1},
            strict=True,
            extra="forbid",
        )
    assert dict(tool.params.model_config) == before
    assert SchemaValidationPolicy().mutate_native_params_model is False

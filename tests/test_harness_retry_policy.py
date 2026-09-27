"""REC-02 deterministic pre-dispatch retry tests; no gateway calls."""

import pytest

from datetime import datetime, timezone

from tau2.harness.contracts import (
    HarnessErrorCode, HarnessStage, Requestor, ToolCallRequest,
    ValidationDecision, ValidationResult, ValidationViolation,
    canonical_json_bytes, canonical_json_sha256,
)
from tau2.harness.dispatcher import A1ValidationExecutor, NativeToolExecutor
from tau2.harness.registry import RegistryTool
from tau2.harness.retry import (
    ProviderFailureKind,
    ProviderGenerationError,
    ProviderGenerationRetryController,
    RetryBudgetExhausted,
    RetryPolicy,
    classify_provider_exception,
)


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def request(call_id="retry-call"):
    return ToolCallRequest.parsed(
        run_id="retry-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=3, logical_call_id=call_id, provider_call_id=call_id,
        requestor=Requestor.ASSISTANT, tool_name="write", arguments={},
        raw_arguments_text="{}", created_at=NOW,
    )


def tool():
    schema = {"type": "object"}
    return RegistryTool(
        tool_name="write", allowed_requestor=Requestor.ASSISTANT,
        toolkit_identity="test", params_model_identity="test",
        params_schema_json=canonical_json_bytes(schema).decode(),
        params_schema_sha256=canonical_json_sha256(schema), native_tool_type="write",
    )


def allow(call):
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.ALLOW, passed_stages=(HarnessStage.SCHEMA,),
        validated_at=NOW,
    )


def reject(call):
    return ValidationResult(
        logical_call_id=call.logical_call_id, request_sha256=call.request_sha256,
        decision=ValidationDecision.REJECT, failed_stage=HarnessStage.DOMAIN_CONSTRAINT,
        failure_code=HarnessErrorCode.DOMAIN_CONSTRAINT_VIOLATION,
        violations=(ValidationViolation(code=HarnessErrorCode.DOMAIN_CONSTRAINT_VIOLATION, path="$", message="rejected"),),
        validated_at=NOW,
    )


def scripted(*outcomes):
    items = iter(outcomes)
    def operation():
        value = next(items)
        if isinstance(value, BaseException):
            raise value
        return value
    return operation


@pytest.mark.parametrize("kind", [
    ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE,
    ProviderFailureKind.TIMEOUT,
    ProviderFailureKind.RATE_LIMIT,
    ProviderFailureKind.HTTP_502,
    ProviderFailureKind.HTTP_503,
    ProviderFailureKind.INTERRUPTION,
])
def test_retryable_provider_failure_once_then_success(kind):
    sleeps = []
    outcome = ProviderGenerationRetryController(sleeper=sleeps.append).execute(
        scripted(ProviderGenerationError(kind), "assistant-response")
    )
    assert outcome.value == "assistant-response"
    assert [a.failure_kind for a in outcome.attempts] == [kind, None]
    assert outcome.attempts[0].retry_scheduled is True
    assert sleeps == [1.0]


def test_retry_budget_is_bounded_and_reports_last_typed_failure():
    calls = 0
    def operation():
        nonlocal calls
        calls += 1
        raise ProviderGenerationError(ProviderFailureKind.TIMEOUT)
    with pytest.raises(RetryBudgetExhausted) as captured:
        ProviderGenerationRetryController(sleeper=lambda _: None).execute(operation)
    assert calls == 3
    assert captured.value.attempts == 3
    assert captured.value.last_error.kind == ProviderFailureKind.TIMEOUT


def test_builtin_timeout_is_normalized_to_provider_timeout():
    classified = classify_provider_exception(TimeoutError("socket deadline"))
    assert classified is not None
    assert classified.kind == ProviderFailureKind.TIMEOUT


def test_litellm_timeout_is_normalized_to_provider_timeout():
    from litellm.exceptions import Timeout as LiteLLMTimeout

    error = LiteLLMTimeout("504 gateway timeout", "model", "openai")
    classified = classify_provider_exception(error)
    assert classified is not None
    assert classified.kind == ProviderFailureKind.TIMEOUT


def test_litellm_timeout_uses_three_attempt_budget_then_exhausts():
    from litellm.exceptions import Timeout as LiteLLMTimeout

    calls, sleeps = 0, []

    def operation():
        nonlocal calls
        calls += 1
        classified = classify_provider_exception(
            LiteLLMTimeout("504 gateway timeout", "model", "openai")
        )
        assert classified is not None
        raise classified

    with pytest.raises(RetryBudgetExhausted) as captured:
        ProviderGenerationRetryController(sleeper=sleeps.append).execute(operation)
    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert captured.value.last_error.kind == ProviderFailureKind.TIMEOUT


def test_litellm_rate_limit_is_normalized_to_typed_provider_failure():
    from litellm.exceptions import RateLimitError

    classified = classify_provider_exception(
        RateLimitError("quota exhausted", "openai", "claude-sonnet-5")
    )
    assert classified is not None
    assert classified.kind == ProviderFailureKind.RATE_LIMIT


def test_structured_http_429_is_normalized_to_typed_provider_failure():
    class StructuredRateLimitError(Exception):
        status_code = 429

    classified = classify_provider_exception(StructuredRateLimitError())
    assert classified is not None
    assert classified.kind == ProviderFailureKind.RATE_LIMIT


def test_structured_http_502_is_normalized_to_typed_provider_failure():
    class StructuredGatewayError(Exception):
        status_code = 502

    classified = classify_provider_exception(StructuredGatewayError())
    assert classified is not None
    assert classified.kind == ProviderFailureKind.HTTP_502


def test_rate_limit_uses_a2_bounded_retry_then_recovers():
    calls, sleeps = 0, []

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            from litellm.exceptions import RateLimitError

            classified = classify_provider_exception(
                RateLimitError("quota exhausted", "openai", "claude-sonnet-5")
            )
            assert classified is not None
            raise classified
        return "provider response"

    outcome = ProviderGenerationRetryController(sleeper=sleeps.append).execute(operation)
    assert calls == 2
    assert sleeps == [1.0]
    assert [attempt.failure_kind for attempt in outcome.attempts] == [
        ProviderFailureKind.RATE_LIMIT,
        None,
    ]


def test_litellm_timeout_then_success_recovers_within_one_generation():
    from litellm.exceptions import Timeout as LiteLLMTimeout

    calls, sleeps = 0, []

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            classified = classify_provider_exception(
                LiteLLMTimeout("504 gateway timeout", "model", "openai")
            )
            assert classified is not None
            raise classified
        return "provider response"

    outcome = ProviderGenerationRetryController(sleeper=sleeps.append).execute(operation)
    assert outcome.value == "provider response"
    assert calls == 2
    assert [attempt.failure_kind for attempt in outcome.attempts] == [
        ProviderFailureKind.TIMEOUT,
        None,
    ]
    assert sleeps == [1.0]


def test_non_typed_or_non_retryable_failure_is_not_retried():
    calls = 0
    def operation():
        nonlocal calls
        calls += 1
        raise ValueError("native domain error must not be reclassified")
    with pytest.raises(ValueError):
        ProviderGenerationRetryController().execute(operation)
    assert calls == 1


def test_policy_backoff_and_budget_are_deterministic():
    policy = RetryPolicy(max_attempts=3, base_backoff_seconds=1.0, max_backoff_seconds=2.0)
    assert policy.delay_before_retry(1) == 1.0
    assert policy.delay_before_retry(2) == 2.0
    assert policy.delay_before_retry(3) == 2.0


def test_native_domain_error_is_materialized_once_not_retried():
    calls = 0
    def native(**_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("User not found")
    call = request("domain")
    result = ProviderGenerationRetryController().execute(
        lambda: A1ValidationExecutor(NativeToolExecutor(native)).materialize(call, allow(call), tool(), completed_at=NOW)
    )
    assert calls == 1
    assert len(result.attempts) == 1
    assert result.value.tool_message.content == "Error: User not found"


def test_harness_reject_is_returned_without_provider_retry_or_dispatch():
    calls = 0
    def native(**_kwargs):
        nonlocal calls
        calls += 1
    call = request("reject")
    outcome = ProviderGenerationRetryController().execute(
        lambda: A1ValidationExecutor(NativeToolExecutor(native)).materialize(call, reject(call), tool(), completed_at=NOW)
    )
    assert calls == 0
    assert outcome.value.dispatch_count == 0
    assert len(outcome.attempts) == 1


def test_write_is_dispatched_once_after_a_retryable_pre_dispatch_generation_failure():
    writes = 0
    provider_calls = 0
    def generation():
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            raise ProviderGenerationError(ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE)
        return "validated assistant response"
    ProviderGenerationRetryController(sleeper=lambda _: None).execute(generation)
    call = request("write-once")
    def write(**_kwargs):
        nonlocal writes
        writes += 1
        return "done"
    result = A1ValidationExecutor(NativeToolExecutor(write)).materialize(call, allow(call), tool(), completed_at=NOW)
    assert provider_calls == 2
    assert writes == 1 and result.dispatch_count == 1

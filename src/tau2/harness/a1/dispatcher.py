"""HAR-05 standalone safe dispatcher and structured error materialization.

No retry, repair, rollback, gateway, native Environment, or orchestrator hook
is implemented here. Native callables are supplied already bound by a caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable, Optional

from pydantic import BaseModel

from tau2.harness.a1.contracts import (
    DispatchResult,
    DispatchStatus,
    HarnessError,
    HarnessErrorCode,
    HarnessStage,
    MaterializationOrigin,
    ParseStatus,
    StateHashSnapshot,
    ToolCallRequest,
    ToolMessageRecord,
    ValidationDecision,
    ValidationResult,
    canonical_json_bytes,
)
from tau2.harness.a1.parser import ParserFailure
from tau2.harness.a1.registry import RegistryTool
from tau2.harness.a1.validation import native_equivalent_domain_error_content
from tau2.harness.a3.runtime import A3QuarantineRequired


class DuplicateExecutionError(RuntimeError):
    """Fail closed; a logical call may be materialized at most once per executor."""

    code = HarnessErrorCode.EXECUTION_DUPLICATE_DISPATCH


class DuplicateDispatchGuard:
    """Episode-owned one-shot guard shared by every bound native callable."""

    def __init__(self):
        self._executed_call_contexts: set[tuple[str, str, int, int, int, str]] = set()

    @staticmethod
    def _key(request: ToolCallRequest) -> tuple[str, str, int, int, int, str]:
        return (
            request.run_id, request.task_id, request.trial_index,
            request.trial_seed, request.turn_index, request.logical_call_id,
        )

    def claim(self, request: ToolCallRequest) -> None:
        execution_key = self._key(request)
        if execution_key in self._executed_call_contexts:
            raise DuplicateExecutionError(
                f"{HarnessErrorCode.EXECUTION_DUPLICATE_DISPATCH.value}: {request.logical_call_id}"
            )
        self._executed_call_contexts.add(execution_key)

    def is_claimed(self, request: ToolCallRequest) -> bool:
        return self._key(request) in self._executed_call_contexts

    def clear(self) -> None:
        self._executed_call_contexts.clear()


def _safe_exception_message(exc: Exception) -> str:
    """Preserve a bounded, redacted diagnostic for sidecar-only HarnessError."""
    message = " ".join(str(exc).split())
    message = re.sub(r"[\w.+-]+@[\w.-]+", "<redacted-email>", message)
    message = re.sub(r"#W\d+", "<redacted-order-id>", message)
    message = re.sub(r"\b\d{13,19}\b", "<redacted-number>", message)
    return message[:256] or "Native callable raised an exception."


def _error_id(logical_call_id: str, request_sha256: str, code: HarnessErrorCode) -> str:
    value = f"herror-v1\0{logical_call_id}\0{request_sha256}\0{code.value}".encode("utf-8")
    return "herr_" + hashlib.sha256(value).hexdigest()[:32]


def _rejection_content(code: HarnessErrorCode) -> str:
    return f"Tool call rejected: {code.value}."


def _execution_error_content() -> str:
    return "Tool execution failed."


def _controlled_native_error_content(exc: Exception) -> str | None:
    """Return the native Environment error payload only when it is safe.

    ``Environment.get_response`` represents a native tool exception as
    ``"Error: {exception}"``.  The evaluator replays trajectories through
    that native path and compares the resulting ToolMessage content exactly.
    Preserving that payload for a bounded, non-sensitive ``ValueError`` keeps
    domain-precondition errors evaluator-compatible.  All other exceptions
    retain the stable harness-safe message.
    """
    raw = str(exc)
    if (
        isinstance(exc, ValueError)
        and raw
        and len(raw) <= 256
        and _safe_exception_message(exc) == raw
    ):
        return f"Error: {raw}"
    return None


def _native_compatible_content(value: Any) -> str:
    """Pure equivalent of native Environment.to_json_str's documented behavior."""
    def process(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump()
        if isinstance(item, str):
            return item
        if item is None:
            return None
        if isinstance(item, (int, float, bool)):
            return str(item)
        if isinstance(item, list):
            return [process(value) for value in item]
        if isinstance(item, tuple):
            return tuple(process(value) for value in item)
        if isinstance(item, dict):
            return {key: process(value) for key, value in item.items()}
        raise ValueError(f"Unsupported native return type: {type(item).__name__}")

    return value if isinstance(value, str) else json.dumps(process(value), default=str)


class NativeToolExecutor:
    """One-shot callable boundary; it has no retry or policy logic."""

    def __init__(
        self,
        native_callable: Callable[..., Any],
        *,
        state_supplier: Optional[Callable[[], Any]] = None,
        duplicate_guard: Optional[DuplicateDispatchGuard] = None,
    ):
        self._native_callable = native_callable
        self._state_supplier = state_supplier
        self._duplicate_guard = duplicate_guard or DuplicateDispatchGuard()

    def state_snapshot(self) -> StateHashSnapshot | None:
        if self._state_supplier is None:
            return None
        return StateHashSnapshot.from_full_db_state(self._state_supplier())

    @property
    def duplicate_guard(self) -> DuplicateDispatchGuard:
        """The episode-owned guard; exposed only for lifecycle wiring checks."""
        return self._duplicate_guard

    def execute_once(self, request: ToolCallRequest) -> Any:
        self._duplicate_guard.claim(request)
        if request.arguments_json is None:
            raise ValueError("allow dispatch requires parsed arguments_json")
        arguments = json.loads(request.arguments_json)
        return self._native_callable(**arguments)


class A1ValidationExecutor:
    """Materialize validated calls without registry lookup or retry."""

    def __init__(self, native_executor: NativeToolExecutor):
        self._native_executor = native_executor

    def materialize(
        self,
        request: ToolCallRequest,
        validation: ValidationResult,
        resolved_tool: RegistryTool,
        *,
        completed_at: datetime,
        before_dispatch: Optional[Callable[[], None]] = None,
        after_native_return: Optional[Callable[[Any], None]] = None,
    ) -> DispatchResult:
        if request.parse_status != ParseStatus.PARSED:
            raise ValueError("use materialize_parser_failure for parser rejection")
        if request.logical_call_id != validation.logical_call_id or request.request_sha256 != validation.request_sha256:
            raise ValueError("ValidationResult does not belong to ToolCallRequest")
        if request.tool_name != resolved_tool.tool_name:
            raise ValueError("RegistryTool does not belong to ToolCallRequest")
        if validation.decision == ValidationDecision.REJECT:
            return self._materialize_reject(
                logical_call_id=request.logical_call_id,
                request_sha256=request.request_sha256,
                requestor=request.requestor,
                failure_code=validation.failure_code,
                failed_stage=validation.failed_stage,
                visible_content=native_equivalent_domain_error_content(validation),
                completed_at=completed_at,
            )
        return self._materialize_allow(
            request, completed_at=completed_at, before_dispatch=before_dispatch,
            after_native_return=after_native_return,
        )

    def materialize_parser_failure(
        self,
        failure: ParserFailure,
        *,
        completed_at: datetime,
    ) -> DispatchResult:
        """No registry/schema/dispatch path is reachable from this method."""
        return self._materialize_reject(
            logical_call_id=failure.synthetic_call_id,
            request_sha256=failure.request_sha256,
            requestor=failure.requestor,
            failure_code=failure.error_code,
            failed_stage=HarnessStage.PARSE,
            completed_at=completed_at,
        )

    def _materialize_reject(
        self,
        *,
        logical_call_id: str,
        request_sha256: str,
        requestor,
        failure_code: HarnessErrorCode | None,
        failed_stage: HarnessStage | None,
        visible_content: str | None = None,
        completed_at: datetime,
    ) -> DispatchResult:
        if failure_code is None or failed_stage is None:
            raise ValueError("reject materialization requires structured validation/parser failure")
        before = self._native_executor.state_snapshot()
        after = self._native_executor.state_snapshot()
        content = visible_content or _rejection_content(failure_code)
        error = HarnessError(
            error_id=_error_id(logical_call_id, request_sha256, failure_code),
            logical_call_id=logical_call_id, stage=failed_stage, code=failure_code,
            message=content, details_json="{}", created_at=completed_at,
        )
        return DispatchResult(
            logical_call_id=logical_call_id, request_sha256=request_sha256,
            validation_decision=ValidationDecision.REJECT, dispatch_attempted=False,
            dispatch_count=0, status=DispatchStatus.NOT_DISPATCHED,
            origin=MaterializationOrigin.HARNESS_REJECT,
            tool_message=ToolMessageRecord(
                id=logical_call_id, requestor=requestor,
                content=content, error=True,
            ),
            harness_error=error, completed_at=completed_at,
            state_before=before, state_after=after,
        )

    def _materialize_allow(
        self,
        request: ToolCallRequest,
        *,
        completed_at: datetime,
        before_dispatch: Optional[Callable[[], None]] = None,
        after_native_return: Optional[Callable[[Any], None]] = None,
    ) -> DispatchResult:
        before = self._native_executor.state_snapshot()
        if before_dispatch is not None:
            # The caller uses this for a durable dispatch_started event. Keep it
            # outside exception containment: telemetry failure must abort before
            # any native callable executes.
            before_dispatch()
        try:
            raw_result = self._native_executor.execute_once(request)
            # A3 receives the raw native value here, before native-compatible
            # success content or ToolMessageRecord can be materialized.
            if after_native_return is not None:
                after_native_return(raw_result)
            content = _native_compatible_content(raw_result)
        except DuplicateExecutionError:
            raise
        except A3QuarantineRequired:
            # The native callable has already executed.  This typed A3 outcome
            # must reach the lifecycle boundary; converting it to a normal tool
            # error would let the agent continue with an ambiguous WRITE.
            raise
        except Exception as exc:
            after = self._native_executor.state_snapshot()
            visible_content = _controlled_native_error_content(exc) or _execution_error_content()
            error = HarnessError(
                error_id=_error_id(request.logical_call_id, request.request_sha256, HarnessErrorCode.UNKNOWN_EXECUTION_ERROR),
                logical_call_id=request.logical_call_id, stage=HarnessStage.NATIVE_TOOL,
                code=HarnessErrorCode.UNKNOWN_EXECUTION_ERROR,
                message="Native callable failed.", details_json="{}", retryable=False,
                repairable=False, cause_type=type(exc).__name__,
                cause_message_redacted=_safe_exception_message(exc), created_at=completed_at,
            )
            return DispatchResult(
                logical_call_id=request.logical_call_id, request_sha256=request.request_sha256,
                validation_decision=ValidationDecision.ALLOW, dispatch_attempted=True,
                dispatch_count=1, status=DispatchStatus.NATIVE_ERROR,
                origin=MaterializationOrigin.NATIVE_DISPATCH,
                tool_message=ToolMessageRecord(
                    id=request.logical_call_id, requestor=request.requestor,
                    content=visible_content, error=True,
                ), harness_error=error, completed_at=completed_at,
                state_before=before, state_after=after,
            )
        after = self._native_executor.state_snapshot()
        return DispatchResult(
            logical_call_id=request.logical_call_id, request_sha256=request.request_sha256,
            validation_decision=ValidationDecision.ALLOW, dispatch_attempted=True,
            dispatch_count=1, status=DispatchStatus.SUCCESS,
            origin=MaterializationOrigin.NATIVE_DISPATCH,
            tool_message=ToolMessageRecord(
                id=request.logical_call_id, requestor=request.requestor, content=content, error=False,
            ), completed_at=completed_at, state_before=before, state_after=after,
        )

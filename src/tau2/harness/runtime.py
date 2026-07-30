"""HAR-07C deterministic execution bridge for one harness-enabled episode.

The bridge is called by ``Orchestrator.step`` only for assistant-originated
ToolCalls. It does not retry, repair, roll back, recover, log traces, or make
gateway requests.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tau2.data_model.message import ToolCall, ToolMessage
from tau2.harness.contracts import (
    HarnessStage,
    Requestor,
    ToolCallRequest,
    ValidationDecision,
    ValidationResult,
    ValidationViolation,
    canonical_json_bytes,
)
from tau2.harness.dispatcher import DuplicateExecutionError
from tau2.harness.episode import EpisodeHarness
from tau2.harness.integration_contracts import (
    OrchestratedHarnessContext,
    TraceFailurePolicy,
    to_native_tool_message,
)
from tau2.harness.registry import RegistryValidator
from tau2.harness.trace import TraceInvariantError, TraceLoggerError
from tau2.harness.validation import StrictSchemaDomainValidator


class HarnessRuntimeBridge:
    """Materialize one assistant tool call without falling through to native ENV."""

    def __init__(self, episode: EpisodeHarness):
        self._episode = episode
        self._registry_validator = RegistryValidator(episode.registry_snapshot)

    def preflight(self) -> None:
        try:
            self._episode.trace_logger.preflight()
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._before_dispatch_failure(exc)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _request_from_native_call(
        tool_call: ToolCall, context: OrchestratedHarnessContext
    ) -> ToolCallRequest:
        if context.requestor != Requestor.ASSISTANT:
            raise ValueError("HAR-07C assistant bridge requires assistant route context")
        return ToolCallRequest.parsed(
            run_id=context.run_id,
            task_id=context.task_id,
            trial_index=context.trial_index,
            trial_seed=context.trial_seed,
            turn_index=context.turn_index,
            logical_call_id=tool_call.id,
            provider_call_id=tool_call.id,
            requestor=context.requestor,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            # HAR-03 already consumed raw provider text. This is a canonical
            # native envelope reconstruction only; it cannot alter arguments.
            raw_arguments_text=canonical_json_bytes(tool_call.arguments).decode("utf-8"),
            created_at=HarnessRuntimeBridge._now(),
        )

    @staticmethod
    def _registry_rejection(request: ToolCallRequest, result, now: datetime) -> ValidationResult:
        return ValidationResult(
            logical_call_id=request.logical_call_id,
            request_sha256=request.request_sha256,
            decision=ValidationDecision.REJECT,
            failed_stage=result.failed_stage,
            failure_code=result.failure_code,
            violations=(ValidationViolation(
                code=result.failure_code,
                path="tool_name",
                message="Tool registry or requestor validation rejected this call.",
                metadata_json="{}",
            ),),
            passed_stages=result.passed_stages,
            validated_at=now,
        )

    def execute_assistant_call(
        self, tool_call: ToolCall, *, context: OrchestratedHarnessContext
    ) -> ToolMessage:
        """Return a native ToolMessage for exactly one routed assistant call."""
        sidecar = self._episode.parser_failure_sidecar(
            turn_index=context.turn_index, requestor=context.requestor
        )
        now = self._now()
        # Synthetic naming is never a routing signal. Membership in the
        # episode-turn sidecar is the sole parser-rejection source of truth.
        if sidecar.contains(tool_call.id, context):
            failure = sidecar.consume(tool_call.id, context)
            result = self._episode.parser_rejection_executor.materialize_parser_failure(
                failure, completed_at=now
            )
            try:
                self._episode.trace_logger.parser_rejection(failure, result)
            except (TraceLoggerError, TraceInvariantError) as exc:
                self._before_dispatch_failure(exc)
            return to_native_tool_message(result.tool_message)
        if sidecar.was_consumed(tool_call.id, context):
            raise RuntimeError("synthetic parser failure was already consumed")

        request = self._request_from_native_call(tool_call, context)
        if self._episode.duplicate_guard.is_claimed(request):
            raise DuplicateExecutionError(
                f"duplicate dispatch blocked before trace emission: {request.logical_call_id}"
            )
        self._trace_before_validation(request)
        registry = self._registry_validator.validate(
            request,
            trusted_route=self._trusted_route(context),
            validated_at=now,
        )
        if not registry.allowed:
            validation = self._registry_rejection(request, registry, now)
            result = self._episode.parser_rejection_executor.materialize(
                request, validation, self._placeholder_tool(request), completed_at=now
            )
            self._trace_validation_reject(request, validation, self._placeholder_tool(request), result)
            return to_native_tool_message(result.tool_message)

        params_model = self._episode.params_model_for(
            requestor=request.requestor, tool_name=request.tool_name or ""
        )
        validation = StrictSchemaDomainValidator.validate(
            request, registry.resolved_tool, params_model=params_model, validated_at=now
        )
        executor = self._episode.executor_for(
            requestor=request.requestor, tool_name=request.tool_name or ""
        )
        if validation.decision == ValidationDecision.REJECT:
            result = executor.materialize(
                request, validation, registry.resolved_tool, completed_at=now
            )
            self._trace_validation_reject(request, validation, registry.resolved_tool, result)
            return to_native_tool_message(result.tool_message)
        try:
            self._episode.trace_logger.validated(request, validation, tool=registry.resolved_tool)
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._before_dispatch_failure(exc)
        result = executor.materialize(
            request, validation, registry.resolved_tool, completed_at=now,
            before_dispatch=lambda: self._trace_dispatch_started(request, validation, registry.resolved_tool),
        )
        try:
            self._episode.trace_logger.dispatch_completed(request, validation, result, tool=registry.resolved_tool)
            self._episode.trace_logger.result_emitted(request, validation, result, tool=registry.resolved_tool)
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._after_dispatch_failure(exc)
        return to_native_tool_message(result.tool_message)

    def _trace_before_validation(self, request: ToolCallRequest) -> None:
        try:
            self._episode.trace_logger.proposed(request)
            self._episode.trace_logger.parsed(request)
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._before_dispatch_failure(exc)

    def _trace_validation_reject(self, request, validation, tool, result) -> None:
        try:
            self._episode.trace_logger.validated(request, validation, tool=tool)
            self._episode.trace_logger.result_emitted(request, validation, result, tool=tool)
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._before_dispatch_failure(exc)

    def _trace_dispatch_started(self, request, validation, tool) -> None:
        try:
            self._episode.trace_logger.dispatch_started(request, validation, tool=tool)
        except (TraceLoggerError, TraceInvariantError) as exc:
            self._before_dispatch_failure(exc)

    def _before_dispatch_failure(self, exc: Exception) -> None:
        error = exc if isinstance(exc, TraceLoggerError) else TraceLoggerError(str(exc))
        TraceFailurePolicy.before_dispatch(self._episode.lifecycle_store, error)

    def _after_dispatch_failure(self, exc: Exception) -> None:
        error = exc if isinstance(exc, TraceLoggerError) else TraceLoggerError(str(exc))
        TraceFailurePolicy.after_dispatch(self._episode.lifecycle_store, error)

    @staticmethod
    def _trusted_route(context: OrchestratedHarnessContext):
        from tau2.harness.contracts import TrustedRouteContext
        return TrustedRouteContext(route_id="orchestrated-llm-agent", requestor=context.requestor)

    @staticmethod
    def _placeholder_tool(request: ToolCallRequest):
        """Registry reject has no resolved tool; HAR-05 only checks its name.

        This method deliberately never exposes the placeholder to a callable:
        reject materialization dispatches zero times.
        """
        from tau2.harness.registry import RegistryTool
        from tau2.harness.contracts import canonical_json_sha256
        schema = {"type": "object"}
        schema_json = canonical_json_bytes(schema).decode("utf-8")
        return RegistryTool(
            tool_name=request.tool_name or "__unknown__",
            allowed_requestor=request.requestor,
            toolkit_identity="harness:registry-reject-placeholder",
            params_model_identity="harness:registry-reject-placeholder",
            params_schema_json=schema_json,
            params_schema_sha256=canonical_json_sha256(schema),
            native_tool_type="generic",
        )

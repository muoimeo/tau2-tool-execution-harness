"""HAR-07B provider tool-call bridge for an explicitly scoped LLMAgent route.

This module parses no text other than the raw provider function arguments via
HAR-03. It performs no registry lookup, schema validation, ToolMessage
construction, dispatcher call, Environment access, or gateway I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from tau2.data_model.message import ToolCall
from tau2.harness.a1.integration_contracts import (
    OrchestratedHarnessContext,
    ParserFailureSidecar,
)
from tau2.harness.a1.parser import ParserBatchResult, ParserNormalizer, RawToolCall
from tau2.harness.a1.contracts import TrustedRouteContext
from tau2.harness.a3.audit import RecoveryAuditTrail
from tau2.harness.a3.contracts import RecoveryAuditEvent, RecoveryDisposition
from tau2.harness.a3.repair import A3Repairer


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def parse_orchestrated_provider_tool_calls(
    provider_tool_calls: Sequence[Any],
    *,
    context: OrchestratedHarnessContext,
    sidecar: ParserFailureSidecar,
    parsed_at: datetime,
    normalizer: ParserNormalizer | None = None,
    repairer: A3Repairer | None = None,
    recovery_audit: RecoveryAuditTrail | None = None,
    repair_lineage: dict[str, Any] | None = None,
) -> tuple[list[ToolCall], ParserBatchResult]:
    """Materialize ordered native routing envelopes after HAR-03 parsing.

    Raw provider argument text is passed to ``ParserNormalizer`` before any
    bridge-level JSON decoding. Valid calls preserve provider ID/name and the
    decoded object semantics. Failures become safe synthetic envelopes held by
    the sidecar; they are not HAR ToolCallRequests and cannot dispatch here.
    """
    raw_calls = []
    for call in provider_tool_calls:
        function = _field(call, "function")
        arguments = _field(function, "arguments")
        raw_calls.append(RawToolCall(
            provider_call_id=_field(call, "id"),
            raw_tool_name=_field(function, "name"),
            raw_arguments_text=arguments if isinstance(arguments, str) else None,
        ))
    raw_calls = tuple(raw_calls)
    parser = normalizer or ParserNormalizer()
    batch = parser.parse(
        raw_calls,
        run_id=context.run_id,
        task_id=context.task_id,
        trial_index=context.trial_index,
        trial_seed=context.trial_seed,
        turn_index=context.turn_index,
        trusted_route=TrustedRouteContext(
            route_id="orchestrated-llm-agent", requestor=context.requestor
        ),
        parsed_at=parsed_at,
    )
    if batch.boundary_error is not None:
        # This cannot be converted into a synthetic ToolCall: no requestor is
        # trusted. The caller must fail closed before entering a native loop.
        raise ValueError(batch.boundary_error.error_code.value)

    failures_by_index = {failure.raw_call_index: failure for failure in batch.failures}
    requests = iter(batch.requests)
    native_calls: list[ToolCall] = []
    for raw_call_index in range(len(raw_calls)):
        failure = failures_by_index.get(raw_call_index)
        if failure is not None:
            # A3 is intentionally downstream of the first A1 parser pass.
            # Valid calls never reach this block, and A3-off takes the original
            # HAR-07B sidecar path without importing a repair result.
            candidate = repairer.try_repair(failure) if repairer is not None else None
            if candidate is not None:
                repaired_batch = parser.parse(
                    (RawToolCall(
                        provider_call_id=failure.raw_call.provider_call_id,
                        raw_tool_name=failure.raw_call.raw_tool_name,
                        raw_arguments_text=candidate.repaired_payload,
                    ),),
                    run_id=context.run_id, task_id=context.task_id,
                    trial_index=context.trial_index, trial_seed=context.trial_seed,
                    turn_index=context.turn_index,
                    trusted_route=TrustedRouteContext(
                        route_id="orchestrated-llm-agent", requestor=context.requestor,
                    ), parsed_at=parsed_at,
                )
                if len(repaired_batch.requests) == 1:
                    request = repaired_batch.requests[0]
                    if repair_lineage is not None:
                        if request.logical_call_id in repair_lineage:
                            raise ValueError("duplicate A3 repair lineage logical call ID")
                        repair_lineage[request.logical_call_id] = candidate
                    if recovery_audit is not None:
                        recovery_audit.append(RecoveryAuditEvent(
                            sequence=recovery_audit.next_sequence,
                            logical_call_id=request.logical_call_id,
                            request_sha256=request.request_sha256,
                            disposition=RecoveryDisposition.NOT_APPLICABLE,
                            original_failure=failure.error_code.value,
                            repair_attempt_count=1, repair_type=candidate.repair_type,
                            original_payload_sha256=candidate.original_payload_sha256,
                            repaired_payload_sha256=candidate.repaired_payload_sha256,
                            validation_outcome="a1_parser_success",
                            dispatch_count=0, write_count=0,
                        ))
                    native_calls.append(ToolCall(
                        id=request.provider_call_id or request.logical_call_id,
                        name=request.tool_name or "",
                        arguments=json.loads(request.arguments_json or "{}"),
                        requestor=request.requestor.value,
                    ))
                    continue
            native_calls.append(sidecar.register(failure))
            continue
        request = next(requests)
        # This decodes HAR-03's canonical arguments JSON, never the raw
        # provider argument text. ToolCall needs an in-memory dict.
        native_calls.append(
            ToolCall(
                id=request.provider_call_id or request.logical_call_id,
                name=request.tool_name or "",
                arguments=json.loads(request.arguments_json or "{}"),
                requestor=request.requestor.value,
            )
        )
    return native_calls, batch

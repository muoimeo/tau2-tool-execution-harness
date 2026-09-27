"""A3 post-dispatch review.  It never retries or replays a native callable."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tau2.harness.a1.contracts import ToolCallRequest
from tau2.harness.a3.audit import RecoveryAuditTrail
from tau2.harness.a3.contracts import RepairCandidate, RecoveryAuditEvent, RecoveryDisposition
from tau2.harness.a3.output_validation import validate_output
from tau2.harness.a3.postconditions import PostconditionPlan, capture_after, capture_before, evaluate


class A3QuarantineRequired(RuntimeError):
    def __init__(self, *, reason: str, side_effect_may_have_occurred: bool):
        super().__init__(reason)
        self.reason = reason
        self.side_effect_may_have_occurred = side_effect_may_have_occurred


@dataclass(frozen=True)
class A3PreDispatchReview:
    plan: PostconditionPlan | None
    is_write: bool


class A3PostDispatchReviewer:
    """Review raw native output before any successful ToolMessage is created."""
    def __init__(self, environment, audit: RecoveryAuditTrail):
        self._environment = environment
        self._audit = audit

    def before_dispatch(self, request: ToolCallRequest, *, native_tool_type: str) -> A3PreDispatchReview:
        arguments = json.loads(request.arguments_json or "{}")
        is_write = native_tool_type == "write"
        return A3PreDispatchReview(
            plan=capture_before(self._environment, tool_name=request.tool_name or "", arguments=arguments) if is_write else None,
            is_write=is_write,
        )

    def after_native_return(
        self,
        request: ToolCallRequest,
        *,
        raw_result: Any,
        returns_model: type[BaseModel],
        review: A3PreDispatchReview,
        repair: RepairCandidate | None = None,
    ) -> None:
        # This is deliberately first: post-state is captured immediately after
        # execute_once() and before output/postcondition validation.
        observed, post_hash = capture_after(self._environment, review.plan)
        output = validate_output(raw_result, returns_model=returns_model)
        postcondition = evaluate(review.plan, observed, post_hash)
        quarantine = None
        if not output.valid:
            quarantine = RecoveryDisposition.QUARANTINED_OUTPUT_INVALID
        elif postcondition.applicable and not postcondition.passed:
            quarantine = RecoveryDisposition.QUARANTINED_POSTCONDITION_FAILED
        disposition = (
            quarantine
            or (RecoveryDisposition.REPAIRED_DISPATCHED if repair else RecoveryDisposition.OUTPUT_VALID)
        )
        self._audit.append(RecoveryAuditEvent(
            sequence=self._audit.next_sequence,
            logical_call_id=request.logical_call_id,
            request_sha256=request.request_sha256,
            disposition=disposition,
            repair_attempt_count=1 if repair else 0,
            repair_type=repair.repair_type if repair else None,
            original_payload_sha256=repair.original_payload_sha256 if repair else None,
            repaired_payload_sha256=repair.repaired_payload_sha256 if repair else None,
            validation_outcome="a1_parser_registry_schema_domain_allow" if repair else None,
            dispatch_count=1,
            write_count=1 if review.is_write else 0,
            output_validation=output,
            postcondition=postcondition,
            quarantine_reason=quarantine.value if quarantine else None,
        ))
        if quarantine:
            raise A3QuarantineRequired(
                reason=quarantine.value,
                side_effect_may_have_occurred=review.is_write,
            )

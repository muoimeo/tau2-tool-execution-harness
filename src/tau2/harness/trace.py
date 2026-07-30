"""HAR-06 append-only, deterministic ToolEvent JSONL telemetry.

This module writes sidecar telemetry only. It does not alter execution results,
dispatch, retry, repair, gateway traffic, or native tau2 runtime behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

from tau2.harness.contracts import (
    DispatchResult,
    HarnessErrorCode,
    Requestor,
    ToolCallRequest,
    ToolEvent,
    ToolEventPhase,
    ValidationDecision,
    ValidationResult,
    canonical_json_bytes,
)
from tau2.harness.parser import ParserFailure
from tau2.harness.registry import RegistryTool


class TraceLoggerError(RuntimeError):
    """A mandatory telemetry write failed; caller must explicitly contain it."""


class TraceInvariantError(ValueError):
    """The caller attempted an invalid trace phase transition or duplicate."""


def _redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    value = re.sub(r"[\w.+-]+@[\w.-]+", "<redacted-email>", value)
    value = re.sub(r"#W\d+", "<redacted-order-id>", value)
    value = re.sub(r"\b\d{10,19}\b", "<redacted-number>", value)
    return value[:256] or None


class TraceLogger:
    """One trace stream with in-memory transition guards and JSONL append writes."""

    def __init__(self, path: Path, *, fixture_raw_retention: bool = False):
        self._path = Path(path)
        self._fixture_raw_retention = fixture_raw_retention
        self._next_sequence = 0
        self._phases: dict[str, tuple[ToolEventPhase, ...]] = {}
        self._closed = False
        self._load_existing_trace()

    def preflight(self) -> None:
        """Verify the per-episode trace path is durably writable before use."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise TraceLoggerError(f"Cannot preflight trace JSONL: {self._path}") from exc

    def summary(self) -> tuple[str, int, int | None]:
        """Return trace SHA-256, event count and last sequence from this logger."""
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise TraceLoggerError(f"Cannot read trace JSONL summary: {self._path}") from exc
        return hashlib.sha256(raw).hexdigest(), len(raw.splitlines()), self._next_sequence - 1 if self._next_sequence else None

    def close(self) -> None:
        self._closed = True

    def _load_existing_trace(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise TraceLoggerError(f"Cannot read existing trace JSONL: {self._path}") from exc
        for line in lines:
            if not line:
                continue
            try:
                event = ToolEvent.model_validate_json(line)
            except Exception as exc:
                raise TraceLoggerError("Existing trace JSONL has invalid ToolEvent") from exc
            if canonical_json_bytes(event.model_dump(mode="json")).decode("utf-8") != line:
                raise TraceLoggerError("Existing trace JSONL is not canonical serialization")
            self._validate_transition(event)
            history = self._phases.get(event.request_sha256, ())
            self._phases[event.request_sha256] = history + (event.phase,)
            self._next_sequence += 1

    @staticmethod
    def _context_from_request(request: ToolCallRequest) -> dict:
        return {
            "run_id": request.run_id,
            "task_id": request.task_id,
            "trial_index": request.trial_index,
            "trial_seed": request.trial_seed,
            "turn_index": request.turn_index,
            "logical_call_id": request.logical_call_id,
            "request_sha256": request.request_sha256,
            "requestor": request.requestor,
            "tool_name": request.tool_name,
        }

    @staticmethod
    def _context_from_failure(failure: ParserFailure) -> dict:
        return {
            "run_id": failure.run_id,
            "task_id": failure.task_id,
            "trial_index": failure.trial_index,
            "trial_seed": failure.trial_seed,
            "turn_index": failure.turn_index,
            "logical_call_id": failure.synthetic_call_id,
            "request_sha256": failure.request_sha256,
            "requestor": failure.requestor,
            "tool_name": failure.raw_call.raw_tool_name,
        }

    @staticmethod
    def _event_id(context: dict, sequence: int, phase: ToolEventPhase) -> str:
        material = canonical_json_bytes({
            "schema": "tool-event/v1", "run_id": context["run_id"],
            "task_id": context["task_id"], "trial_index": context["trial_index"],
            "trial_seed": context["trial_seed"], "turn_index": context["turn_index"],
            "logical_call_id": context["logical_call_id"], "request_sha256": context["request_sha256"],
            "sequence": sequence, "phase": phase.value,
        })
        return "tevt_" + hashlib.sha256(material).hexdigest()[:32]

    def _expected_phase(self, request_sha256: str) -> ToolEventPhase | None:
        history = self._phases.get(request_sha256, ())
        if not history:
            return ToolEventPhase.PROPOSED
        last = history[-1]
        if last == ToolEventPhase.PROPOSED:
            return ToolEventPhase.PARSED
        if last == ToolEventPhase.PARSED:
            return ToolEventPhase.VALIDATED
        if last == ToolEventPhase.VALIDATED:
            return None  # selected from decision by append() below
        if last == ToolEventPhase.DISPATCH_STARTED:
            return ToolEventPhase.DISPATCH_COMPLETED
        if last == ToolEventPhase.DISPATCH_COMPLETED:
            return ToolEventPhase.RESULT_EMITTED
        return None

    def append(self, event: ToolEvent) -> ToolEvent:
        if self._closed:
            raise TraceLoggerError("Cannot append a closed trace logger")
        self._validate_transition(event)
        encoded = canonical_json_bytes(event.model_dump(mode="json")) + b"\n"
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise TraceLoggerError(f"Cannot append trace JSONL: {self._path}") from exc
        history = self._phases.get(event.request_sha256, ())
        self._phases[event.request_sha256] = history + (event.phase,)
        self._next_sequence += 1
        return event

    def _validate_transition(self, event: ToolEvent) -> None:
        history = self._phases.get(event.request_sha256, ())
        expected = self._expected_phase(event.request_sha256)
        if event.phase in history:
            raise TraceInvariantError(f"duplicate trace phase: {event.phase.value}")
        if history and history[-1] == ToolEventPhase.PARSED and event.phase == ToolEventPhase.RESULT_EMITTED:
            # Parser failure skips validation and dispatch only when it has a parse code.
            if event.failure_code not in {
                HarnessErrorCode.PARSE_MALFORMED_JSON,
                HarnessErrorCode.PARSE_ARGUMENTS_NOT_OBJECT,
                HarnessErrorCode.PARSE_RESPONSE_STRUCTURE_INVALID,
            }:
                raise TraceInvariantError("parsed-to-result skip requires parser failure code")
        elif history and history[-1] == ToolEventPhase.VALIDATED:
            if event.decision == ValidationDecision.REJECT and event.phase != ToolEventPhase.RESULT_EMITTED:
                raise TraceInvariantError("reject trace cannot emit dispatch phases")
            if event.decision == ValidationDecision.ALLOW and event.phase != ToolEventPhase.DISPATCH_STARTED:
                raise TraceInvariantError("allow trace must start dispatch after validation")
        elif expected != event.phase:
            raise TraceInvariantError(
                f"invalid trace transition: expected {expected.value if expected else 'terminal'}, got {event.phase.value}"
            )
        if event.sequence != self._next_sequence:
            raise TraceInvariantError("ToolEvent sequence must equal next append sequence")

    def _build_event(
        self,
        context: dict,
        phase: ToolEventPhase,
        *,
        tool_type: Optional[str] = None,
        decision: ValidationDecision | None = None,
        failure_code: HarnessErrorCode | None = None,
        dispatch: DispatchResult | None = None,
        raw_payload: dict | None = None,
    ) -> ToolEvent:
        event_context = dict(context)
        timestamp = event_context.pop("timestamp")
        error = dispatch.harness_error if dispatch else None
        raw_json = None
        if self._fixture_raw_retention and raw_payload is not None:
            raw_json = canonical_json_bytes(raw_payload).decode("utf-8")
        return ToolEvent(
            event_id=self._event_id(context, self._next_sequence, phase),
            sequence=self._next_sequence, phase=phase,
            native_tool_type=tool_type, decision=decision, failure_code=failure_code,
            dispatch_status=dispatch.status if dispatch else None,
            dispatch_count=dispatch.dispatch_count if dispatch else None,
            state_hash_before=dispatch.state_before.sha256 if dispatch and dispatch.state_before else None,
            state_hash_after=dispatch.state_after.sha256 if dispatch and dispatch.state_after else None,
            exception_type=error.cause_type if error else None,
            exception_message_redacted=_redact_text(error.cause_message_redacted) if error else None,
            raw_retention="fixture" if raw_json is not None else "none",
            raw_payload_json=raw_json,
            **event_context,
            timestamp=dispatch.completed_at if dispatch else timestamp,
        )

    def proposed(self, request: ToolCallRequest, *, tool: RegistryTool | None = None) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": request.created_at}
        return self.append(self._build_event(
            context, ToolEventPhase.PROPOSED,
            tool_type=tool.native_tool_type if tool else None,
            raw_payload={"raw_arguments_text": request.raw_arguments_text},
        ))

    def parsed(self, request: ToolCallRequest, *, tool: RegistryTool | None = None) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": request.created_at}
        return self.append(self._build_event(context, ToolEventPhase.PARSED, tool_type=tool.native_tool_type if tool else None))

    def validated(self, request: ToolCallRequest, validation: ValidationResult, *, tool: RegistryTool) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": validation.validated_at}
        return self.append(self._build_event(
            context, ToolEventPhase.VALIDATED, tool_type=tool.native_tool_type,
            decision=validation.decision, failure_code=validation.failure_code,
        ))

    def dispatch_started(self, request: ToolCallRequest, validation: ValidationResult, *, tool: RegistryTool) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": validation.validated_at}
        return self.append(self._build_event(context, ToolEventPhase.DISPATCH_STARTED, tool_type=tool.native_tool_type, decision=validation.decision))

    def dispatch_completed(self, request: ToolCallRequest, validation: ValidationResult, dispatch: DispatchResult, *, tool: RegistryTool) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": dispatch.completed_at}
        return self.append(self._build_event(context, ToolEventPhase.DISPATCH_COMPLETED, tool_type=tool.native_tool_type, decision=validation.decision, failure_code=dispatch.harness_error.code if dispatch.harness_error else None, dispatch=dispatch))

    def result_emitted(self, request: ToolCallRequest, validation: ValidationResult, dispatch: DispatchResult, *, tool: RegistryTool) -> ToolEvent:
        context = self._context_from_request(request) | {"timestamp": dispatch.completed_at}
        return self.append(self._build_event(context, ToolEventPhase.RESULT_EMITTED, tool_type=tool.native_tool_type, decision=validation.decision, failure_code=dispatch.harness_error.code if dispatch.harness_error else validation.failure_code, dispatch=dispatch))

    def parser_rejection(self, failure: ParserFailure, dispatch: DispatchResult) -> tuple[ToolEvent, ToolEvent, ToolEvent]:
        context = self._context_from_failure(failure) | {"timestamp": dispatch.completed_at}
        proposed = self.append(self._build_event(context, ToolEventPhase.PROPOSED, raw_payload={"raw_arguments_text": failure.raw_call.raw_arguments_text}))
        parsed = self.append(self._build_event(context, ToolEventPhase.PARSED, failure_code=failure.error_code))
        result = self.append(self._build_event(context, ToolEventPhase.RESULT_EMITTED, decision=ValidationDecision.REJECT, failure_code=failure.error_code, dispatch=dispatch))
        return proposed, parsed, result

"""Pre-HAR-07 integration contracts; no native runtime hook is installed."""
from __future__ import annotations

import hashlib
import os
import sys
from enum import Enum
from pathlib import Path

from pydantic import Field

from tau2.data_model.message import ToolCall, ToolMessage
from tau2.harness.contracts import ContractModel, Requestor, ToolMessageRecord, canonical_json_bytes
from tau2.harness.parser import ParserFailure
from tau2.harness.trace import TraceLoggerError

RESERVED_INVALID_TOOL_NAME = "__harness_invalid_tool_call__"


class OrchestratedHarnessContext(ContractModel):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    requestor: Requestor


def use_harness_parser(context: OrchestratedHarnessContext | None) -> bool:
    """Only an explicit orchestrated caller opts into HAR-03."""
    return context is not None


class ParserFailureSidecar:
    """Episode-scoped bridge from a parser rejection to a native envelope."""

    def __init__(self, context: OrchestratedHarnessContext):
        self._context = context
        self._by_call_id: dict[str, ParserFailure] = {}
        self._consumed_call_ids: set[str] = set()

    def register(self, failure: ParserFailure) -> ToolCall:
        expected = (
            failure.run_id, failure.task_id, failure.trial_index,
            failure.trial_seed, failure.turn_index, failure.requestor,
        )
        actual = (
            self._context.run_id, self._context.task_id, self._context.trial_index,
            self._context.trial_seed, self._context.turn_index, self._context.requestor,
        )
        if expected != actual:
            raise ValueError("parser failure does not belong to this episode turn context")
        call_id = failure.synthetic_call_id
        if call_id in self._by_call_id:
            raise ValueError("duplicate parser failure synthetic ID")
        self._by_call_id[call_id] = failure
        return ToolCall(
            id=call_id,
            name=failure.raw_call.raw_tool_name or RESERVED_INVALID_TOOL_NAME,
            arguments={},
            requestor=failure.requestor.value,
        )

    def consume(
        self, call_id: str, context: OrchestratedHarnessContext
    ) -> ParserFailure:
        """Remove one routed parser failure; all other reads fail closed."""
        if context != self._context:
            raise ValueError("parser failure consume context mismatch")
        try:
            failure = self._by_call_id.pop(call_id)
        except KeyError as exc:
            raise KeyError("unknown or already-consumed parser failure ID") from exc
        self._consumed_call_ids.add(call_id)
        return failure

    def contains(self, call_id: str, context: OrchestratedHarnessContext) -> bool:
        """Membership check used solely to select parser-rejection routing."""
        if context != self._context:
            raise ValueError("parser failure membership context mismatch")
        return call_id in self._by_call_id

    def was_consumed(self, call_id: str, context: OrchestratedHarnessContext) -> bool:
        if context != self._context:
            raise ValueError("parser failure membership context mismatch")
        return call_id in self._consumed_call_ids

    def clear(self) -> None:
        self._by_call_id.clear()
        self._consumed_call_ids.clear()


def to_native_tool_message(record: ToolMessageRecord) -> ToolMessage:
    return ToolMessage(
        id=record.id, role="tool", content=record.content,
        requestor=record.requestor.value, error=record.error,
    )


class LifecycleStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    ABORTED = "aborted"
    FAILED_BEFORE_DISPATCH = "failed_before_dispatch"
    FAILED_AFTER_DISPATCH = "failed_after_dispatch"
    FAILED_OPERATIONAL = "failed_operational"


class EpisodeIdentity(ContractModel):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    episode_attempt: int = Field(ge=1)

    def filename_stem(self) -> str:
        return f"task-{self.task_id}__trial-{self.trial_index:03d}__seed-{self.trial_seed}__attempt-{self.episode_attempt:03d}"


class EpisodeLifecycle(ContractModel):
    schema_version: str = "episode-lifecycle/v1"
    identity: EpisodeIdentity
    status: LifecycleStatus
    trace_filename: str
    trace_sha256: str | None = None
    trace_event_count: int | None = None
    trace_last_sequence: int | None = None
    side_effect_may_have_occurred: bool = False
    operational_failure: str | None = None
    final_operational_outcome: str | None = None


class EpisodeLifecycleStore:
    def __init__(self, directory: Path, identity: EpisodeIdentity):
        self.directory, self.identity = Path(directory), identity
        stem = identity.filename_stem()
        self.trace_path = self.directory / f"{stem}.jsonl"
        self.lifecycle_path = self.directory / f"{stem}.lifecycle.json"

    def write(self, lifecycle: EpisodeLifecycle) -> None:
        if lifecycle.identity != self.identity:
            raise ValueError("lifecycle identity mismatch")
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.lifecycle_path.with_suffix(".tmp")
        data = canonical_json_bytes(lifecycle.model_dump(mode="json"))
        with temp.open("wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, self.lifecycle_path)

    def open(self) -> EpisodeLifecycle:
        if self.lifecycle_path.exists():
            raise ValueError("episode lifecycle already exists; preflight resume first")
        lifecycle = EpisodeLifecycle(identity=self.identity, status=LifecycleStatus.OPEN, trace_filename=self.trace_path.name)
        self.write(lifecycle)
        return lifecycle

    def read(self) -> EpisodeLifecycle:
        lifecycle = EpisodeLifecycle.model_validate_json(
            self.lifecycle_path.read_text(encoding="utf-8")
        )
        if lifecycle.identity != self.identity:
            raise ValueError("lifecycle identity mismatch")
        if lifecycle.trace_filename != self.trace_path.name:
            raise ValueError("lifecycle trace filename mismatch")
        return lifecycle

    def close(self, *, trace_sha256: str | None = None, trace_event_count: int | None = None, trace_last_sequence: int | None = None, outcome: str = "succeeded") -> EpisodeLifecycle:
        raw = self.trace_path.read_bytes()
        lifecycle = EpisodeLifecycle(identity=self.identity, status=LifecycleStatus.CLOSED, trace_filename=self.trace_path.name, trace_sha256=trace_sha256 or hashlib.sha256(raw).hexdigest(), trace_event_count=trace_event_count if trace_event_count is not None else len(raw.splitlines()), trace_last_sequence=trace_last_sequence, final_operational_outcome=outcome)
        self.write(lifecycle); return lifecycle

    def _mark_aborted(self, lifecycle: EpisodeLifecycle, *, reason: str) -> EpisodeLifecycle:
        if lifecycle.status == LifecycleStatus.CLOSED:
            raise ValueError("closed episode cannot be marked aborted")
        aborted = EpisodeLifecycle(
            identity=self.identity,
            status=LifecycleStatus.ABORTED,
            trace_filename=lifecycle.trace_filename,
            trace_sha256=lifecycle.trace_sha256,
            trace_event_count=lifecycle.trace_event_count,
            side_effect_may_have_occurred=lifecycle.side_effect_may_have_occurred,
            operational_failure=reason,
        )
        self.write(aborted)
        return aborted

    def prepare_resume(self) -> EpisodeIdentity | None:
        """Validate closed traces or atomically retire an incomplete attempt.

        Returns ``None`` only for a valid closed attempt (the caller must skip
        it).  A non-closed attempt is never resumed mid-episode: its lifecycle
        is marked aborted and the returned identity has a new attempt number.
        The caller must construct a fresh environment for that identity.
        """
        lifecycle = self.read()
        if lifecycle.status == LifecycleStatus.CLOSED:
            raw = self.trace_path.read_bytes()
            if lifecycle.trace_sha256 != hashlib.sha256(raw).hexdigest() or lifecycle.trace_event_count != len(raw.splitlines()):
                raise ValueError("closed lifecycle/trace mismatch")
            return None
        self._mark_aborted(lifecycle, reason="incomplete attempt retired before rerun")
        return EpisodeIdentity(
            run_id=self.identity.run_id, task_id=self.identity.task_id,
            trial_index=self.identity.trial_index, trial_seed=self.identity.trial_seed,
            episode_attempt=self.identity.episode_attempt + 1,
        )


class TraceFailurePolicy:
    """A1 research-integrity containment policy; it never rolls back state."""

    @staticmethod
    def before_dispatch(store: EpisodeLifecycleStore, error: TraceLoggerError) -> None:
        TraceFailurePolicy._best_effort_mark(store, LifecycleStatus.FAILED_BEFORE_DISPATCH, False, error)
        raise error

    @staticmethod
    def after_dispatch(store: EpisodeLifecycleStore, error: TraceLoggerError) -> None:
        TraceFailurePolicy._best_effort_mark(store, LifecycleStatus.FAILED_AFTER_DISPATCH, True, error)
        raise error

    @staticmethod
    def _best_effort_mark(store: EpisodeLifecycleStore, status: LifecycleStatus, side_effect: bool, error: Exception) -> None:
        try:
            lifecycle = store.read()
            if lifecycle.status != LifecycleStatus.OPEN:
                raise ValueError("trace failure policy requires an open episode")
            store.write(EpisodeLifecycle(
                identity=store.identity, status=status,
                trace_filename=lifecycle.trace_filename,
                side_effect_may_have_occurred=side_effect,
                operational_failure=f"TraceLoggerError: {type(error).__name__}",
                final_operational_outcome="failed",
            ))
        except Exception as lifecycle_error:
            print(f"HAR-07D lifecycle write failed after telemetry failure: {lifecycle_error}", file=sys.stderr)

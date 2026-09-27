"""Pre-HAR-07 integration contracts; no native runtime hook is installed."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import Field

from tau2.data_model.message import ToolCall, ToolMessage
from tau2.harness.a1.contracts import ContractModel, Requestor, ToolMessageRecord, canonical_json_bytes
from tau2.harness.a1.parser import ParserFailure
from tau2.harness.a1.trace import TraceLoggerError

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
    provider_retry_filename: str | None = None
    provider_retry_event_count: int | None = None
    provider_retry_last_sequence: int | None = None
    provider_retry_sha256: str | None = None


class EpisodeLifecycleStore:
    def __init__(
        self,
        directory: Path,
        identity: EpisodeIdentity,
        *,
        max_unsafe_manual_resumes: int = 2,
    ):
        if max_unsafe_manual_resumes < 0:
            raise ValueError("max unsafe manual resumes must be non-negative")
        self.directory, self.identity = Path(directory), identity
        self.max_unsafe_manual_resumes = max_unsafe_manual_resumes
        stem = identity.filename_stem()
        self.trace_path = self.directory / f"{stem}.jsonl"
        self.provider_retry_path = self.directory / f"{stem}.provider-retry.jsonl"
        self.lifecycle_path = self.directory / f"{stem}.lifecycle.json"

    def write(self, lifecycle: EpisodeLifecycle) -> None:
        if lifecycle.identity != self.identity:
            raise ValueError("lifecycle identity mismatch")
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.lifecycle_path.with_suffix(".tmp")
        data = canonical_json_bytes(lifecycle.model_dump(mode="json"))
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
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

    def provider_retry_snapshot(self) -> dict:
        exists = self.provider_retry_path.exists()
        raw = self.provider_retry_path.read_bytes() if exists else b""
        lines = raw.splitlines()
        return {"provider_retry_filename": self.provider_retry_path.name if exists else None, "provider_retry_event_count": len(lines) if exists else None, "provider_retry_last_sequence": json.loads(lines[-1])["sequence"] if lines else None, "provider_retry_sha256": hashlib.sha256(raw).hexdigest() if exists else None}

    def trace_snapshot(self) -> dict:
        """Read the durable append-only trace for every terminal lifecycle.

        TraceLogger fsyncs each event.  An operational failure can therefore
        happen after dispatch without the normal results-commit close path.
        Terminalization must snapshot the trace file itself rather than copy
        the still-open lifecycle's empty fields.
        """
        exists = self.trace_path.exists()
        raw = self.trace_path.read_bytes() if exists else b""
        lines = raw.splitlines()
        # ``terminalize`` is also used to retire incomplete/legacy attempts.
        # Preserve their artifact bytes without pretending a placeholder line
        # is a valid ToolEvent.  Normal results commit and trace validation
        # remain the strict paths for a closed successful episode.
        try:
            last_value = json.loads(lines[-1]).get("sequence") if lines else None
            last_sequence = last_value if isinstance(last_value, int) else None
        except (json.JSONDecodeError, TypeError):
            last_sequence = None
        return {
            "trace_filename": self.trace_path.name if exists else None,
            "trace_sha256": hashlib.sha256(raw).hexdigest() if exists else None,
            "trace_event_count": len(lines) if exists else None,
            "trace_last_sequence": last_sequence,
        }

    def terminalize(self, lifecycle: EpisodeLifecycle, *, status: LifecycleStatus, outcome: str | None = None, error: str | None = None, side_effect: bool | None = None) -> EpisodeLifecycle:
        snapshot = self.trace_snapshot()
        # A lifecycle created before the trace preflight still needs a stable
        # filename; preserve that contract if the file genuinely does not yet
        # exist.  Once a trace exists, its durable bytes are authoritative.
        if snapshot["trace_filename"] is None:
            snapshot["trace_filename"] = lifecycle.trace_filename
            snapshot["trace_sha256"] = lifecycle.trace_sha256
            snapshot["trace_event_count"] = lifecycle.trace_event_count
            snapshot["trace_last_sequence"] = lifecycle.trace_last_sequence
        value = EpisodeLifecycle(identity=self.identity, status=status, side_effect_may_have_occurred=lifecycle.side_effect_may_have_occurred if side_effect is None else side_effect, operational_failure=error, final_operational_outcome=outcome, **snapshot, **self.provider_retry_snapshot())
        self.write(value)
        return value

    def close(self, *, trace_sha256: str | None = None, trace_event_count: int | None = None, trace_last_sequence: int | None = None, outcome: str = "succeeded") -> EpisodeLifecycle:
        raw = self.trace_path.read_bytes()
        open_lifecycle = EpisodeLifecycle(identity=self.identity, status=LifecycleStatus.OPEN, trace_filename=self.trace_path.name, trace_sha256=trace_sha256 or hashlib.sha256(raw).hexdigest(), trace_event_count=trace_event_count if trace_event_count is not None else len(raw.splitlines()), trace_last_sequence=trace_last_sequence)
        return self.terminalize(open_lifecycle, status=LifecycleStatus.CLOSED, outcome=outcome)

    def _mark_aborted(self, lifecycle: EpisodeLifecycle, *, reason: str) -> EpisodeLifecycle:
        if lifecycle.status == LifecycleStatus.CLOSED:
            raise ValueError("closed episode cannot be marked aborted")
        return self.terminalize(lifecycle, status=LifecycleStatus.ABORTED, error=reason)

    def _validate_failed_attempt_integrity(self) -> None:
        lifecycle = self.read()
        if lifecycle.status != LifecycleStatus.FAILED_OPERATIONAL:
            raise ValueError("unsafe resume requires failed_operational lifecycle")
        if not lifecycle.side_effect_may_have_occurred:
            raise ValueError("unsafe resume authorization requires side_effect_may_have_occurred=true")
        if not self.trace_path.exists():
            raise ValueError("unsafe resume requires an episode tool trace")
        trace_lines = self.trace_path.read_bytes().splitlines()
        try:
            trace_rows = [json.loads(line) for line in trace_lines]
        except json.JSONDecodeError as exc:
            raise ValueError("unsafe resume tool trace is invalid") from exc
        if [row.get("sequence") for row in trace_rows] != list(range(len(trace_rows))):
            raise ValueError("unsafe resume tool trace sequence is not contiguous")
        if not any(
            row.get("phase") == "dispatch_completed"
            and row.get("native_tool_type") == "write"
            for row in trace_rows
        ):
            raise ValueError("unsafe resume authorization requires a completed WRITE trace event")
        if not lifecycle.provider_retry_filename or not self.provider_retry_path.exists():
            raise ValueError("unsafe resume requires a provider-retry sidecar")
        provider_raw = self.provider_retry_path.read_bytes()
        provider_lines = provider_raw.splitlines()
        if lifecycle.provider_retry_sha256 != hashlib.sha256(provider_raw).hexdigest():
            raise ValueError("unsafe resume provider-retry SHA mismatch")
        if lifecycle.provider_retry_event_count != len(provider_lines):
            raise ValueError("unsafe resume provider-retry event count mismatch")
        try:
            provider_rows = [json.loads(line) for line in provider_lines]
        except json.JSONDecodeError as exc:
            raise ValueError("unsafe resume provider-retry sidecar is invalid") from exc
        if [row.get("sequence") for row in provider_rows] != list(range(1, len(provider_rows) + 1)):
            raise ValueError("unsafe resume provider-retry sequence is not contiguous")
        grouped: dict[str, list[int]] = {}
        for row in provider_rows:
            grouped.setdefault(row["generation_id"], []).append(row["provider_attempt"])
        if any(attempts != list(range(1, len(attempts) + 1)) or len(attempts) > 3 for attempts in grouped.values()):
            raise ValueError("unsafe resume provider attempt progression is invalid")
        if lifecycle.provider_retry_last_sequence != (provider_rows[-1]["sequence"] if provider_rows else None):
            raise ValueError("unsafe resume provider-retry last sequence mismatch")

    @property
    def manual_resume_audit_path(self) -> Path:
        return self.directory / "manual-unsafe-resume-audit.jsonl"

    def unsafe_resume_authorization_count(self) -> int:
        """Count bounded human-authorized reruns for this logical task/trial."""
        if not self.manual_resume_audit_path.exists():
            return 0
        count = 0
        for line in self.manual_resume_audit_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if (
                row.get("outcome") in {"authorized_new_attempt", "authorized_no_write_new_attempt"}
                and row.get("task_id") == self.identity.task_id
                and row.get("trial_index") == self.identity.trial_index
            ):
                count += 1
        return count

    def _consume_unsafe_resume_authorization(self, authorization: str) -> None:
        self._validate_failed_attempt_integrity()
        consumed = self.unsafe_resume_authorization_count()
        if consumed >= self.max_unsafe_manual_resumes:
            raise ValueError(
                "unsafe manual-resume budget exhausted for "
                f"{self.identity.task_id}:{self.identity.trial_index}: "
                f"{consumed}>={self.max_unsafe_manual_resumes}"
            )
        if self.manual_resume_audit_path.exists():
            for line in self.manual_resume_audit_path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                row = json.loads(line)
                if row.get("authorization") == authorization:
                    raise ValueError("unsafe resume authorization was already used")
        parts = authorization.split(":")
        if len(parts) != 3:
            raise ValueError("unsafe resume authorization must be TASK:TRIAL:ATTEMPT")
        task_id, trial, attempt = parts
        if (
            task_id != self.identity.task_id
            or int(trial) != self.identity.trial_index
            or int(attempt) != self.identity.episode_attempt
        ):
            raise ValueError("unsafe resume authorization identity mismatch")
        row = {
            "schema_version": "manual-unsafe-resume/v1",
            "authorization": authorization,
            "task_id": task_id,
            "trial_index": int(trial),
            "failed_attempt": int(attempt),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "side_effect_may_have_occurred": True,
            "outcome": "authorized_new_attempt",
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.manual_resume_audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _validate_no_write_failed_attempt_integrity(self) -> None:
        """Validate an explicit rerun only when trace evidence proves no WRITE ran."""
        lifecycle = self.read()
        if lifecycle.status != LifecycleStatus.FAILED_OPERATIONAL:
            raise ValueError("no-WRITE resume requires failed_operational lifecycle")
        if not self.trace_path.exists():
            raise ValueError("no-WRITE resume requires an episode tool trace")
        trace_raw = self.trace_path.read_bytes()
        if lifecycle.trace_sha256 != hashlib.sha256(trace_raw).hexdigest():
            raise ValueError("no-WRITE resume tool trace SHA mismatch")
        if lifecycle.trace_event_count != len(trace_raw.splitlines()):
            raise ValueError("no-WRITE resume tool trace event count mismatch")
        try:
            trace_rows = [json.loads(line) for line in trace_raw.splitlines()]
        except json.JSONDecodeError as exc:
            raise ValueError("no-WRITE resume tool trace is invalid") from exc
        if [row.get("sequence") for row in trace_rows] != list(range(len(trace_rows))):
            raise ValueError("no-WRITE resume tool trace sequence is not contiguous")
        if any(row.get("phase") == "dispatch_completed" and row.get("native_tool_type") == "write" for row in trace_rows):
            raise ValueError("no-WRITE resume is forbidden after completed WRITE")
        if lifecycle.provider_retry_filename:
            raw = self.provider_retry_path.read_bytes()
            if lifecycle.provider_retry_sha256 != hashlib.sha256(raw).hexdigest() or lifecycle.provider_retry_event_count != len(raw.splitlines()):
                raise ValueError("no-WRITE resume provider-retry snapshot mismatch")

    def _consume_no_write_resume_authorization(self, authorization: str) -> None:
        self._validate_no_write_failed_attempt_integrity()
        if self.unsafe_resume_authorization_count() >= self.max_unsafe_manual_resumes:
            raise ValueError("manual resume budget exhausted for no-WRITE episode")
        if self.manual_resume_audit_path.exists() and any(
            json.loads(line).get("authorization") == authorization
            for line in self.manual_resume_audit_path.read_text(encoding="utf-8").splitlines() if line
        ):
            raise ValueError("no-WRITE resume authorization was already used")
        parts = authorization.split(":")
        if len(parts) != 3 or (parts[0], int(parts[1]), int(parts[2])) != (
            self.identity.task_id, self.identity.trial_index, self.identity.episode_attempt
        ):
            raise ValueError("no-WRITE resume authorization identity mismatch")
        row = {
            "schema_version": "manual-no-write-resume/v1", "authorization": authorization,
            "task_id": self.identity.task_id, "trial_index": self.identity.trial_index,
            "failed_attempt": self.identity.episode_attempt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "side_effect_may_have_occurred": self.read().side_effect_may_have_occurred,
            "evidence_no_completed_write": True, "outcome": "authorized_no_write_new_attempt",
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.manual_resume_audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def prepare_resume(self, *, authorization: str | None = None, no_write_authorization: str | None = None) -> EpisodeIdentity | None:
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
        # A failed-operational attempt is terminal evidence.  Resume creates a
        # new attempt but must never relabel or rewrite this per-attempt file.
        if lifecycle.status == LifecycleStatus.FAILED_OPERATIONAL:
            if lifecycle.side_effect_may_have_occurred:
                if authorization is not None and no_write_authorization is not None:
                    raise ValueError("only one resume authorization type may be supplied")
                if authorization is not None:
                    self._consume_unsafe_resume_authorization(authorization)
                elif no_write_authorization is not None:
                    self._consume_no_write_resume_authorization(no_write_authorization)
                else:
                    raise ValueError("unsafe failed episode requires explicit manual resume authorization")
            if lifecycle.provider_retry_filename:
                raw = self.provider_retry_path.read_bytes()
                if lifecycle.provider_retry_sha256 != hashlib.sha256(raw).hexdigest() or lifecycle.provider_retry_event_count != len(raw.splitlines()):
                    raise ValueError("failed lifecycle/provider-retry snapshot mismatch")
                rows = [json.loads(line) for line in raw.splitlines()]
                if [row["sequence"] for row in rows] != list(range(1, len(rows)+1)):
                    raise ValueError("failed provider-retry sequence mismatch")
            next_identity = EpisodeIdentity(run_id=self.identity.run_id, task_id=self.identity.task_id, trial_index=self.identity.trial_index, trial_seed=self.identity.trial_seed, episode_attempt=self.identity.episode_attempt + 1)
            if (self.directory / f"{next_identity.filename_stem()}.lifecycle.json").exists():
                raise ValueError("unsafe resume target attempt already exists")
            return next_identity
        if lifecycle.provider_retry_filename:
            raw = self.provider_retry_path.read_bytes()
            if lifecycle.provider_retry_sha256 != hashlib.sha256(raw).hexdigest() or lifecycle.provider_retry_event_count != len(raw.splitlines()):
                raise ValueError("failed lifecycle/provider-retry snapshot mismatch")
            rows = [json.loads(line) for line in raw.splitlines()]
            if [row["sequence"] for row in rows] != list(range(1, len(rows)+1)):
                raise ValueError("failed provider-retry sequence mismatch")
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


def validate_unsafe_resume_authorization(
    settings,
    authorization: str,
    *,
    scheduled_task_ids: set[str],
) -> None:
    """Preflight one exact A2 unsafe-resume authorization without consuming it."""
    parts = authorization.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError("unsafe resume authorization must be TASK:TRIAL:ATTEMPT")
    task_id, trial_text, attempt_text = parts
    try:
        trial, attempt = int(trial_text), int(attempt_text)
    except ValueError as exc:
        raise ValueError("unsafe resume authorization trial/attempt must be integers") from exc
    if trial < 0 or trial >= len(settings.config_payload["trial_seeds"]):
        raise ValueError("unsafe resume authorization trial is outside the frozen run")
    if attempt < 1:
        raise ValueError("unsafe resume authorization attempt must be positive")
    if task_id not in scheduled_task_ids:
        raise ValueError("unsafe resume authorization names an unscheduled task")
    identity = EpisodeIdentity(
        run_id=settings.run_id,
        task_id=task_id,
        trial_index=trial,
        trial_seed=settings.config_payload["trial_seeds"][trial],
        episode_attempt=attempt,
    )
    store = EpisodeLifecycleStore(
        settings.trace_root,
        identity,
        max_unsafe_manual_resumes=settings.config_payload.get("max_unsafe_manual_resumes", 2),
    )
    if not store.lifecycle_path.exists():
        raise ValueError("authorized unsafe episode lifecycle does not exist")
    store._validate_failed_attempt_integrity()
    if store.unsafe_resume_authorization_count() >= store.max_unsafe_manual_resumes:
        raise ValueError(
            "unsafe manual-resume budget exhausted for "
            f"{task_id}:{trial}: "
            f"{store.unsafe_resume_authorization_count()}>={store.max_unsafe_manual_resumes}"
        )
    next_path = settings.trace_root / f"{EpisodeIdentity(run_id=settings.run_id, task_id=task_id, trial_index=trial, trial_seed=identity.trial_seed, episode_attempt=attempt + 1).filename_stem()}.lifecycle.json"
    if next_path.exists():
        raise ValueError("authorized unsafe resume target attempt already exists")


def validate_no_write_resume_authorization(
    settings,
    authorization: str,
    *,
    scheduled_task_ids: set[str],
) -> None:
    """Preflight an explicit exact-tuple rerun when evidence proves no WRITE."""
    parts = authorization.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError("no-WRITE resume authorization must be TASK:TRIAL:ATTEMPT")
    task_id, trial_text, attempt_text = parts
    try:
        trial, attempt = int(trial_text), int(attempt_text)
    except ValueError as exc:
        raise ValueError("no-WRITE resume authorization trial/attempt must be integers") from exc
    if task_id not in scheduled_task_ids or trial < 0 or trial >= len(settings.config_payload["trial_seeds"]) or attempt < 1:
        raise ValueError("no-WRITE resume authorization identity is outside the frozen run")
    identity = EpisodeIdentity(
        run_id=settings.run_id, task_id=task_id, trial_index=trial,
        trial_seed=settings.config_payload["trial_seeds"][trial], episode_attempt=attempt,
    )
    store = EpisodeLifecycleStore(
        settings.trace_root, identity,
        max_unsafe_manual_resumes=settings.config_payload.get("max_unsafe_manual_resumes", 2),
    )
    if not store.lifecycle_path.exists():
        raise ValueError("authorized no-WRITE episode lifecycle does not exist")
    store._validate_no_write_failed_attempt_integrity()
    if store.unsafe_resume_authorization_count() >= store.max_unsafe_manual_resumes:
        raise ValueError("manual resume budget exhausted for no-WRITE episode")
    next_identity = EpisodeIdentity(
        run_id=settings.run_id, task_id=task_id, trial_index=trial,
        trial_seed=identity.trial_seed, episode_attempt=attempt + 1,
    )
    if (settings.trace_root / f"{next_identity.filename_stem()}.lifecycle.json").exists():
        raise ValueError("authorized no-WRITE resume target attempt already exists")


def validate_unresolved_episode_skip(
    settings,
    *,
    task_id: str,
    trial_index: int,
    trial_seed: int,
    max_unsafe_manual_resumes: int,
) -> EpisodeLifecycle:
    """Validate an orchestration-only skip after unsafe budget exhaustion.

    This never consumes authorization, creates an attempt, mutates lifecycle
    state, or produces a benchmark result. It only permits the scheduler to
    account for one exact logical task/trial tuple as unresolved.
    """
    identity = settings.identity(
        task_id=str(task_id), trial_index=trial_index, trial_seed=trial_seed
    )
    prefix = (
        f"task-{identity.task_id}__trial-{identity.trial_index:03d}"
        f"__seed-{identity.trial_seed}__attempt-"
    )
    candidates = []
    for path in settings.trace_root.glob(f"{prefix}*.lifecycle.json"):
        candidate = EpisodeLifecycle.model_validate_json(
            path.read_text(encoding="utf-8")
        ).identity
        if candidate.run_id == identity.run_id:
            candidates.append(candidate)
    if candidates:
        identity = max(candidates, key=lambda value: value.episode_attempt)
    store = EpisodeLifecycleStore(
        settings.trace_root,
        identity,
        max_unsafe_manual_resumes=max_unsafe_manual_resumes,
    )
    if not store.lifecycle_path.exists():
        raise ValueError("unresolved-episode skip requires a persisted lifecycle")
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.FAILED_OPERATIONAL:
        raise ValueError("unresolved-episode skip requires failed_operational lifecycle")
    if not lifecycle.side_effect_may_have_occurred:
        raise ValueError("unresolved-episode skip requires unsafe side-effect lifecycle")
    store._validate_failed_attempt_integrity()
    consumed = store.unsafe_resume_authorization_count()
    if consumed < max_unsafe_manual_resumes:
        raise ValueError(
            "unresolved-episode skip requires exhausted unsafe-resume budget: "
            f"{consumed}<{max_unsafe_manual_resumes}"
        )
    return lifecycle


_SAFE_FAILURE_AMENDMENT = "a2-safe-failure-protocol-amendment-v1.json"
_SAFE_FAILURE_AUDIT = "manual-safe-unresolved-audit.jsonl"


def _safe_failure_amendment_path(settings) -> Path:
    return settings.trace_root / _SAFE_FAILURE_AMENDMENT


def _safe_failure_audit_path(settings) -> Path:
    return settings.trace_root / _SAFE_FAILURE_AUDIT


def _validate_failed_provider_sidecar(store: EpisodeLifecycleStore) -> None:
    """Validate retry evidence for a safe failed attempt without requiring a WRITE."""
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.FAILED_OPERATIONAL:
        raise ValueError("safe unresolved declaration requires failed_operational lifecycle")
    if not lifecycle.provider_retry_filename or not store.provider_retry_path.exists():
        raise ValueError("safe unresolved declaration requires a provider-retry sidecar")
    raw = store.provider_retry_path.read_bytes()
    lines = raw.splitlines()
    if lifecycle.provider_retry_sha256 != hashlib.sha256(raw).hexdigest():
        raise ValueError("safe unresolved provider-retry SHA mismatch")
    if lifecycle.provider_retry_event_count != len(lines):
        raise ValueError("safe unresolved provider-retry event count mismatch")
    rows = [json.loads(line) for line in lines]
    if [row.get("sequence") for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("safe unresolved provider-retry sequence mismatch")
    grouped: dict[str, list[int]] = {}
    for row in rows:
        grouped.setdefault(row["generation_id"], []).append(row["provider_attempt"])
    if any(attempts != list(range(1, len(attempts) + 1)) or len(attempts) > 3
           for attempts in grouped.values()):
        raise ValueError("safe unresolved provider attempt progression is invalid")
    if lifecycle.provider_retry_last_sequence != (rows[-1]["sequence"] if rows else None):
        raise ValueError("safe unresolved provider-retry last sequence mismatch")


def _latest_lifecycle_store(settings, *, task_id: str, trial_index: int, trial_seed: int,
                            max_unsafe_manual_resumes: int) -> EpisodeLifecycleStore:
    initial = settings.identity(task_id=task_id, trial_index=trial_index, trial_seed=trial_seed)
    prefix = (f"task-{initial.task_id}__trial-{initial.trial_index:03d}"
              f"__seed-{initial.trial_seed}__attempt-")
    candidates: list[EpisodeIdentity] = []
    for path in settings.trace_root.glob(f"{prefix}*.lifecycle.json"):
        candidate = EpisodeLifecycle.model_validate_json(path.read_text(encoding="utf-8")).identity
        if candidate.run_id == initial.run_id:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("safe unresolved declaration requires a persisted lifecycle")
    identity = max(candidates, key=lambda value: value.episode_attempt)
    return EpisodeLifecycleStore(settings.trace_root, identity,
                                 max_unsafe_manual_resumes=max_unsafe_manual_resumes)


def _safe_failed_attempts(settings, *, task_id: str, trial_index: int, trial_seed: int,
                          max_unsafe_manual_resumes: int) -> list[EpisodeLifecycleStore]:
    initial = settings.identity(task_id=task_id, trial_index=trial_index, trial_seed=trial_seed)
    prefix = (f"task-{initial.task_id}__trial-{initial.trial_index:03d}"
              f"__seed-{initial.trial_seed}__attempt-")
    stores: list[EpisodeLifecycleStore] = []
    for path in settings.trace_root.glob(f"{prefix}*.lifecycle.json"):
        lifecycle = EpisodeLifecycle.model_validate_json(path.read_text(encoding="utf-8"))
        if lifecycle.identity.run_id != initial.run_id:
            continue
        if lifecycle.status == LifecycleStatus.FAILED_OPERATIONAL and not lifecycle.side_effect_may_have_occurred:
            store = EpisodeLifecycleStore(settings.trace_root, lifecycle.identity,
                                          max_unsafe_manual_resumes=max_unsafe_manual_resumes)
            _validate_failed_provider_sidecar(store)
            stores.append(store)
    return sorted(stores, key=lambda item: item.identity.episode_attempt)


def _write_or_validate_safe_failure_amendment(settings, *, max_safe_failures_per_logical_episode: int) -> None:
    """Persist the v4 orchestration amendment without rewriting frozen config."""
    if max_safe_failures_per_logical_episode < 1:
        raise ValueError("safe-failure protocol amendment requires a positive per-episode budget")
    config_bytes = settings.config_path.read_bytes()
    payload = {
        "schema_version": "a2-safe-failure-protocol-amendment/v1",
        "base_frozen_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "scope": "per_logical_episode",
        "max_safe_failures_per_logical_episode": max_safe_failures_per_logical_episode,
        "result_policy": "no_simulation_run_no_reward_for_operationally_unresolved",
    }
    encoded = canonical_json_bytes(payload)
    path = _safe_failure_amendment_path(settings)
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("safe-failure protocol amendment does not match this frozen root")
        return
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def terminalize_safe_failure_budget(
    settings,
    *,
    task_id: str,
    trial_index: int,
    trial_seed: int,
    max_safe_failures_per_logical_episode: int,
) -> dict:
    """Idempotently terminalize one *safe* exhausted logical episode.

    This is supervisor/accounting only: it validates the immutable failed
    attempts, appends exactly one terminal audit row, and never creates an
    Environment, a provider call, a new attempt, or a benchmark result.
    """
    if max_safe_failures_per_logical_episode < 1:
        raise ValueError("safe-failure terminalization requires a positive budget")
    max_unsafe = settings.config_payload.get("max_unsafe_manual_resumes", 2)
    attempts = _safe_failed_attempts(
        settings,
        task_id=str(task_id),
        trial_index=trial_index,
        trial_seed=trial_seed,
        max_unsafe_manual_resumes=max_unsafe,
    )
    if len(attempts) < max_safe_failures_per_logical_episode:
        raise ValueError("safe-failure terminalization requires an exhausted per-episode budget")
    latest = attempts[-1]
    lifecycle = latest.read()
    # _safe_failed_attempts has already validated every provider sidecar. The
    # latest attempt must still be the latest lifecycle, otherwise an unsafe or
    # closed attempt has superseded it and cannot be silently terminalized.
    actual_latest = _latest_lifecycle_store(
        settings,
        task_id=str(task_id),
        trial_index=trial_index,
        trial_seed=trial_seed,
        max_unsafe_manual_resumes=max_unsafe,
    )
    if actual_latest.identity.episode_attempt != latest.identity.episode_attempt:
        raise ValueError("safe-failure terminalization latest attempt is no longer safe")
    if lifecycle.side_effect_may_have_occurred:
        raise ValueError("safe-failure terminalization cannot apply after a WRITE side effect")

    audit_path = _safe_failure_audit_path(settings)
    existing: dict | None = None
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if (
                row.get("outcome") == "safe_failure_budget_terminalized"
                and row.get("task_id") == str(task_id)
                and row.get("trial_index") == trial_index
                and row.get("trial_seed") == trial_seed
            ):
                existing = row
                break
    if existing is not None:
        if (
            existing.get("failed_attempt") != latest.identity.episode_attempt
            or existing.get("safe_failure_count") != len(attempts)
            or existing.get("max_safe_failures_per_logical_episode")
            != max_safe_failures_per_logical_episode
            or existing.get("scope") != "per_logical_episode"
        ):
            raise ValueError("safe-failure terminalization audit conflicts with immutable attempts")
        return existing

    row = {
        "schema_version": "safe-operational-terminalization/v2",
        "terminalization": "automatic_supervisor",
        "scope": "per_logical_episode",
        "task_id": str(task_id),
        "trial_index": trial_index,
        "trial_seed": trial_seed,
        "failed_attempt": latest.identity.episode_attempt,
        # This is intentionally episode attempts, not provider attempts.
        "safe_failure_count": len(attempts),
        "max_safe_failures_per_logical_episode": max_safe_failures_per_logical_episode,
        "latest_side_effect_may_have_occurred": False,
        "outcome": "safe_failure_budget_terminalized",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    settings.trace_root.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def declare_safe_failure_unresolved(
    settings,
    declaration: str,
    *,
    scheduled_task_ids: set[str],
    max_safe_failures_per_logical_episode: int,
) -> tuple[str, int]:
    """Append one explicit, exact-tuple terminal safe-failure declaration.

    The declaration neither edits old lifecycle/trace files nor creates a new
    attempt. It is an accounting decision after the frozen per-episode limit
    has already been reached.
    """
    parts = declaration.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError("safe unresolved declaration must be TASK:TRIAL:ATTEMPT")
    task_id, trial_text, attempt_text = parts
    try:
        trial, attempt = int(trial_text), int(attempt_text)
    except ValueError as exc:
        raise ValueError("safe unresolved declaration trial/attempt must be integers") from exc
    if task_id not in scheduled_task_ids:
        raise ValueError("safe unresolved declaration names an unscheduled task")
    if trial < 0 or trial >= len(settings.config_payload["trial_seeds"]) or attempt < 1:
        raise ValueError("safe unresolved declaration identity is outside the frozen run")
    seed = settings.config_payload["trial_seeds"][trial]
    max_unsafe = settings.config_payload.get("max_unsafe_manual_resumes", 2)
    latest = _latest_lifecycle_store(settings, task_id=task_id, trial_index=trial,
                                     trial_seed=seed, max_unsafe_manual_resumes=max_unsafe)
    lifecycle = latest.read()
    if lifecycle.status != LifecycleStatus.FAILED_OPERATIONAL:
        raise ValueError("safe unresolved declaration requires failed_operational lifecycle")
    if latest.identity.episode_attempt != attempt:
        raise ValueError("safe unresolved declaration must name the latest failed attempt")
    if lifecycle.side_effect_may_have_occurred:
        latest._validate_failed_attempt_integrity()
    else:
        _validate_failed_provider_sidecar(latest)
    safe_attempts = _safe_failed_attempts(settings, task_id=task_id, trial_index=trial,
                                          trial_seed=seed, max_unsafe_manual_resumes=max_unsafe)
    if len(safe_attempts) < max_safe_failures_per_logical_episode:
        raise ValueError("safe unresolved declaration requires exhausted per-episode safe-failure budget")
    _write_or_validate_safe_failure_amendment(
        settings, max_safe_failures_per_logical_episode=max_safe_failures_per_logical_episode
    )
    audit_path = _safe_failure_audit_path(settings)
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if row.get("declaration") == declaration or (
                row.get("task_id") == task_id and row.get("trial_index") == trial
            ):
                raise ValueError("safe unresolved declaration was already recorded for this logical episode")
    row = {
        "schema_version": "manual-safe-unresolved/v1",
        "declaration": declaration,
        "task_id": task_id,
        "trial_index": trial,
        "failed_attempt": attempt,
        "safe_failure_count": len(safe_attempts),
        "max_safe_failures_per_logical_episode": max_safe_failures_per_logical_episode,
        "latest_side_effect_may_have_occurred": lifecycle.side_effect_may_have_occurred,
        "outcome": "safe_failure_budget_terminalized",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    settings.trace_root.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return task_id, trial


def validate_a2_unresolved_episode_skip(
    settings,
    *,
    task_id: str,
    trial_index: int,
    trial_seed: int,
    max_unsafe_manual_resumes: int,
) -> tuple[EpisodeLifecycle, str]:
    """Validate either frozen unsafe exhaustion or an explicit safe terminalization."""
    try:
        return (
            validate_unresolved_episode_skip(
                settings, task_id=task_id, trial_index=trial_index, trial_seed=trial_seed,
                max_unsafe_manual_resumes=max_unsafe_manual_resumes,
            ),
            "unsafe_resume_budget_exhausted",
        )
    except ValueError as unsafe_error:
        audit_path = _safe_failure_audit_path(settings)
        if not audit_path.exists():
            raise unsafe_error
        latest = _latest_lifecycle_store(settings, task_id=str(task_id), trial_index=trial_index,
                                         trial_seed=trial_seed,
                                         max_unsafe_manual_resumes=max_unsafe_manual_resumes)
        lifecycle = latest.read()
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if (
                row.get("outcome") == "safe_failure_budget_terminalized"
                and row.get("task_id") == str(task_id)
                and row.get("trial_index") == trial_index
                and row.get("trial_seed", trial_seed) == trial_seed
                and row.get("failed_attempt") == latest.identity.episode_attempt
            ):
                if lifecycle.side_effect_may_have_occurred:
                    latest._validate_failed_attempt_integrity()
                else:
                    _validate_failed_provider_sidecar(latest)
                return lifecycle, "safe_failure_budget_exhausted"
        raise unsafe_error

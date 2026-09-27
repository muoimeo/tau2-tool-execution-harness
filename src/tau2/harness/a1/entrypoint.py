"""EXP-02 A1 entrypoint configuration and episode lifecycle helpers.

This module is deliberately inert unless ``--a1-harness`` is selected by the
CLI caller.  It owns A1-only configuration sidecars and never changes native
results, evaluator inputs, retry policy, or environment semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from tau2.environment.environment import Environment
from tau2.harness.a1.contracts import canonical_json_bytes
from tau2.harness.a1.episode import ClosedEpisodeSkip, EpisodeHarness, EpisodeHarnessFactory
from tau2.harness.a1.integration_contracts import EpisodeIdentity, EpisodeLifecycle, EpisodeLifecycleStore, LifecycleStatus


class A1EntrypointError(ValueError):
    """A1 configuration or persisted-lifecycle invariant was violated."""


@dataclass(frozen=True)
class A1RunSettings:
    """Frozen A1-v1 settings needed to identify every episode deterministically."""

    trace_root: Path
    run_id: str
    config_payload: dict

    @classmethod
    def create(
        cls,
        *,
        trace_root: Path,
        domain: str,
        task_ids: Sequence[str],
        agent: str,
        user: str,
        llm_agent: str | None,
        llm_user: str | None,
        num_trials: int,
        trial_seeds: Sequence[int],
        max_steps: int,
        max_errors: int,
        max_concurrency: int,
        root_seed: int | None,
        llm_args_agent: dict | None,
        llm_args_user: dict | None,
        a2_retry: bool = False,
        a3_recovery: bool = False,
        a2_max_operational_failures: int = 3,
        a2_safe_operational_failure_scope: str = "global_root",
        a2_max_unsafe_manual_resumes: int = 2,
    ) -> "A1RunSettings":
        if max_concurrency != 1:
            raise A1EntrypointError("A1 v1 requires max_concurrency=1")
        if agent != "llm_agent":
            raise A1EntrypointError("A1 v1 supports only agent=llm_agent")
        if a2_max_operational_failures < 0:
            raise A1EntrypointError("A2 max operational failures must be non-negative")
        if a2_safe_operational_failure_scope not in {"global_root", "per_logical_episode"}:
            raise A1EntrypointError("A2 safe operational-failure scope is invalid")
        if a2_safe_operational_failure_scope != "global_root" and not a2_retry:
            raise A1EntrypointError("A2 safe operational-failure scope requires A2 retry")
        if (a2_safe_operational_failure_scope == "per_logical_episode"
                and a2_max_operational_failures < 1):
            raise A1EntrypointError("per-logical A2 safe-failure budget must be positive")
        if a2_max_unsafe_manual_resumes < 0:
            raise A1EntrypointError("A2 max unsafe manual resumes must be non-negative")
        if a3_recovery and not a2_retry:
            raise A1EntrypointError("A3 recovery requires A2 retry")
        payload = {
            "schema_version": "a1-run-config/v1",
            "harness_mode": "validation-v1",
            "domain": domain,
            "task_ids": list(task_ids),
            "agent": agent,
            "user": user,
            "agent_model": llm_agent,
            "user_model": llm_user,
            # Hashes bind provider configuration without persisting credentials.
            "agent_llm_args_sha256": hashlib.sha256(canonical_json_bytes(llm_args_agent or {})).hexdigest(),
            "user_llm_args_sha256": hashlib.sha256(canonical_json_bytes(llm_args_user or {})).hexdigest(),
            # Temperature is non-sensitive execution configuration and is
            # retained explicitly; credential-bearing provider arguments stay
            # represented only by the hashes above.
            "agent_temperature": (llm_args_agent or {}).get("temperature"),
            "user_temperature": (llm_args_user or {}).get("temperature"),
            "num_trials": num_trials,
            "trial_seeds": list(trial_seeds),
            "root_seed": root_seed,
            "max_concurrency": max_concurrency,
            "max_steps": max_steps,
            "max_errors": max_errors,
            "retry": a2_retry,
            "repair": a3_recovery,
            "rollback": False,
            "recovery": a3_recovery,
        }
        # Keep A1-only frozen-config bytes stable.  This field is an A2 policy,
        # not an A1 behavior change.
        if a2_retry:
            payload["max_operational_failures"] = a2_max_operational_failures
            # Explicit only for v3+. Omitting the legacy default preserves
            # historical A0/A1/A2 frozen-config bytes and semantics.
            if a2_safe_operational_failure_scope != "global_root":
                payload["safe_operational_failure_scope"] = a2_safe_operational_failure_scope
            payload["max_unsafe_manual_resumes"] = a2_max_unsafe_manual_resumes
            # These restate the already-implemented A2 retry contract in the
            # frozen sidecar; they do not introduce a new runtime policy.
            payload["provider_internal_num_retries"] = 0
            payload["provider_max_attempts"] = 3
            payload["provider_backoff_seconds"] = [1.0, 2.0]
            # v3 is an additive, safe response-shape/provenance telemetry
            # schema. Existing frozen v2 roots remain readable and untouched.
            payload["provider_retry_telemetry_schema"] = "provider-observability/v1"
            payload["provider_observability_schema"] = "provider-observability/v1"
            payload["provider_retry_raw_retention"] = "none"
            payload["provider_empty_structural_observability"] = {
                "schema_version": "provider-empty-structure/v1",
                "enabled_for": "empty_content_and_no_tool_calls_only",
                "raw_retention": "none",
            }
            payload["retry_eligible_provider_failures"] = {
                "PROVIDER_EMPTY_ASSISTANT_MESSAGE": ["empty_content_and_no_tool_calls"],
                "PROVIDER_TIMEOUT": ["builtins.TimeoutError", "litellm.exceptions.Timeout"],
                "PROVIDER_RATE_LIMIT": ["structured_status_code_429", "litellm.exceptions.RateLimitError"],
                "PROVIDER_HTTP_502": ["structured_status_code_502"],
                "PROVIDER_HTTP_503": ["structured_status_code_503"],
                "PROVIDER_INTERRUPTION": ["ConnectionError"],
            }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:16]
        return cls(
            trace_root=Path(trace_root),
            run_id=f"a1v1-{domain}-{digest}",
            config_payload=payload,
        )

    @property
    def config_path(self) -> Path:
        return self.trace_root / "a1-run-config.json"

    def write_or_validate_config(self) -> None:
        """Write one canonical A1 config sidecar, or fail on a mismatch."""
        encoded = canonical_json_bytes(self.config_payload)
        self.trace_root.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            if self.config_path.read_bytes() != encoded:
                raise A1EntrypointError("A1 trace root belongs to a different frozen run configuration")
            return
        temporary = self.config_path.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.config_path)

    def identity(self, *, task_id: str, trial_index: int, trial_seed: int) -> EpisodeIdentity:
        return EpisodeIdentity(
            run_id=self.run_id,
            task_id=str(task_id),
            trial_index=trial_index,
            trial_seed=trial_seed,
            episode_attempt=1,
        )


def default_trace_root(*, save_to: Path) -> Path:
    """Derive an A1-only deterministic trace directory from the result path."""
    return save_to.parent / f"{save_to.stem}.a1-traces"


def latest_episode_identity(settings: A1RunSettings, identity: EpisodeIdentity) -> EpisodeIdentity:
    """Resolve the latest persisted attempt for one logical task/trial/seed."""
    prefix = (
        f"task-{identity.task_id}__trial-{identity.trial_index:03d}"
        f"__seed-{identity.trial_seed}__attempt-"
    )
    candidates: list[EpisodeIdentity] = []
    for path in settings.trace_root.glob(f"{prefix}*.lifecycle.json"):
        lifecycle = EpisodeLifecycle.model_validate_json(path.read_text(encoding="utf-8"))
        candidate = lifecycle.identity
        if (
            candidate.run_id == identity.run_id
            and candidate.task_id == identity.task_id
            and candidate.trial_index == identity.trial_index
            and candidate.trial_seed == identity.trial_seed
        ):
            candidates.append(candidate)
    return max(candidates, key=lambda value: value.episode_attempt) if candidates else identity


def validate_closed_episode_skip(settings: A1RunSettings, identity: EpisodeIdentity) -> bool:
    """Validate a closed trace before native results resume skips its episode.

    A missing lifecycle means the episode has not been harness-executed.  An
    incomplete lifecycle is an integrity error: it must be rerun from a fresh
    environment rather than silently skipped because a results row exists.
    """
    identity = latest_episode_identity(settings, identity)
    store = EpisodeLifecycleStore(
        settings.trace_root,
        identity,
        max_unsafe_manual_resumes=settings.config_payload.get("max_unsafe_manual_resumes", 2),
    )
    if not store.lifecycle_path.exists():
        return False
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.CLOSED:
        raise A1EntrypointError("cannot skip a native-resumed episode with an incomplete A1 lifecycle")
    raw = store.trace_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if lifecycle.trace_sha256 != digest or lifecycle.trace_event_count != len(raw.splitlines()):
        raise A1EntrypointError("closed A1 lifecycle/trace mismatch")
    if settings.config_payload["retry"]:
        _validate_provider_sidecar(settings, store, lifecycle, require_success=True)
    return True


def _validate_provider_sidecar(settings, store, lifecycle, *, require_success: bool) -> None:
    path = store.provider_retry_path
    if not path.exists(): raise A1EntrypointError("A2 provider-retry sidecar missing")
    raw = path.read_bytes(); lines = raw.splitlines(); rows = [json.loads(line) for line in lines]
    if lifecycle.status == LifecycleStatus.CLOSED and (lifecycle.provider_retry_sha256 != hashlib.sha256(raw).hexdigest() or lifecycle.provider_retry_event_count != len(lines)):
        raise A1EntrypointError("closed A2 lifecycle/provider-retry hash/count mismatch")
    if [r["sequence"] for r in rows] != list(range(1, len(rows)+1)): raise A1EntrypointError("provider-retry sequence is not contiguous")
    grouped = {}
    for row in rows: grouped.setdefault(row["generation_id"], []).append(row["provider_attempt"])
    if any(v != list(range(1, len(v)+1)) or len(v) > 3 for v in grouped.values()): raise A1EntrypointError("provider-retry generation attempt ordering invalid")
    if require_success and not any(row["outcome"] == "success" for row in rows):
        raise A1EntrypointError("succeeded A2 episode requires a successful provider event")


def validate_a2_episode_before_results_commit(settings: A1RunSettings, identity: EpisodeIdentity) -> None:
    if not settings.config_payload["retry"]: return
    identity = latest_episode_identity(settings, identity)
    store = EpisodeLifecycleStore(settings.trace_root, identity)
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.OPEN: raise A1EntrypointError("A2 results commit requires open lifecycle")
    _validate_provider_sidecar(settings, store, lifecycle, require_success=True)


def finalize_episode_after_results_commit(
    settings: A1RunSettings, identity: EpisodeIdentity
) -> EpisodeLifecycle:
    """Close an A1 lifecycle only after its native result row is durable."""
    identity = latest_episode_identity(settings, identity)
    store = EpisodeLifecycleStore(settings.trace_root, identity)
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.OPEN:
        raise A1EntrypointError(
            "A1 results commit requires an open episode lifecycle"
        )
    if not store.trace_path.exists():
        raise A1EntrypointError("A1 results commit requires an episode trace")
    lines = store.trace_path.read_bytes().splitlines()
    if not lines:
        raise A1EntrypointError("A1 results commit requires a non-empty episode trace")
    try:
        last_sequence = json.loads(lines[-1])['sequence']
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise A1EntrypointError("A1 trace final event is invalid") from exc
    if not isinstance(last_sequence, int) or last_sequence != len(lines) - 1:
        raise A1EntrypointError("A1 trace sequence is not contiguous at results commit")
    return store.close(
        trace_event_count=len(lines), trace_last_sequence=last_sequence, outcome="succeeded"
    )


def mark_episode_operational_failure(
    settings: A1RunSettings,
    identity: EpisodeIdentity,
    error: BaseException,
    *,
    side_effect_may_have_occurred: bool,
) -> None:
    """Record a post-orchestrator failure without overwriting terminal state."""
    identity = latest_episode_identity(settings, identity)
    store = EpisodeLifecycleStore(settings.trace_root, identity)
    if not store.lifecycle_path.exists():
        return
    lifecycle = store.read()
    if lifecycle.status != LifecycleStatus.OPEN:
        # The orchestrator can terminalize a provider failure before the batch
        # boundary has inspected the completed tool trace.  The batch boundary
        # may only *escalate* this conservative flag when it observes a WRITE;
        # it never downgrades a terminal lifecycle or relabels it.
        if (
            lifecycle.status == LifecycleStatus.FAILED_OPERATIONAL
            and side_effect_may_have_occurred
            and not lifecycle.side_effect_may_have_occurred
        ):
            store.terminalize(
                lifecycle,
                status=LifecycleStatus.FAILED_OPERATIONAL,
                outcome=lifecycle.final_operational_outcome or "failed",
                error=lifecycle.operational_failure or f"{type(error).__name__}: {error}",
                side_effect=True,
            )
        return
    store.terminalize(lifecycle, status=LifecycleStatus.FAILED_OPERATIONAL, outcome="failed", error=f"{type(error).__name__}: {error}", side_effect=side_effect_may_have_occurred)


def create_episode_harness(
    *,
    settings: A1RunSettings,
    task_id: str,
    trial_index: int,
    trial_seed: int,
    environment_factory: Callable[[], Environment],
    unsafe_resume_authorization: str | None = None,
    no_write_resume_authorization: str | None = None,
    a3_recovery: bool = False,
) -> EpisodeHarness | ClosedEpisodeSkip:
    """Create or safely resume exactly one fresh episode-scoped harness."""
    factory = EpisodeHarnessFactory(
        trace_root=settings.trace_root,
        environment_factory=environment_factory,
        max_unsafe_manual_resumes=settings.config_payload.get("max_unsafe_manual_resumes", 2),
    )
    identity = latest_episode_identity(settings, settings.identity(
        task_id=task_id,
        trial_index=trial_index,
        trial_seed=trial_seed,
    ))
    return factory.create(
        identity,
        unsafe_resume_authorization=unsafe_resume_authorization,
        no_write_resume_authorization=no_write_resume_authorization,
        a3_recovery=a3_recovery,
    )

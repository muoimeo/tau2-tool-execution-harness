"""Offline evidence for the A2 v4 per-logical-episode safe-failure amendment."""
import json
import random

import pytest

from tau2.harness.a1.entrypoint import A1RunSettings
from tau2.harness.a1.integration_contracts import (
    EpisodeIdentity,
    EpisodeLifecycleStore,
    LifecycleStatus,
    declare_safe_failure_unresolved,
    validate_a2_unresolved_episode_skip,
    terminalize_safe_failure_budget,
)
from tau2.harness.a2.retry import ProviderFailureKind, ProviderRetrySidecar, RetryAttempt
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.run import load_tasks, run_tasks
import tau2.run as run_module
from tau2.utils.display import ConsoleDisplay


def _settings(tmp_path):
    settings = A1RunSettings.create(
        trace_root=tmp_path / "traces", domain="mock", task_ids=["78", "79"],
        agent="llm_agent", user="user_simulator", llm_agent="agent", llm_user="user",
        num_trials=1, trial_seeds=[626729], max_steps=5, max_errors=2,
        max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={},
        a2_retry=True, a2_max_operational_failures=3, a2_max_unsafe_manual_resumes=2,
    )
    settings.write_or_validate_config()
    return settings


def _safe_failed_attempt(settings, attempt: int):
    identity = EpisodeIdentity(
        run_id=settings.run_id, task_id="78", trial_index=0, trial_seed=626729,
        episode_attempt=attempt,
    )
    store = EpisodeLifecycleStore(settings.trace_root, identity, max_unsafe_manual_resumes=2)
    opened = store.open()
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id=f"g-{attempt}",
                                   turn_index=attempt, episode_attempt=attempt)
    for provider_attempt in range(1, 4):
        sidecar.record(RetryAttempt(
            provider_attempt, ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE,
            provider_attempt < 3, 1.0 if provider_attempt == 1 else 2.0 if provider_attempt == 2 else 0.0,
        ))
    store.terminalize(opened, status=LifecycleStatus.FAILED_OPERATIONAL,
                      outcome="failed", error="exhausted", side_effect=False)


def _safe_failed_attempt_for(settings, *, task_id: str, trial: int, seed: int, attempt: int):
    identity = EpisodeIdentity(
        run_id=settings.run_id, task_id=task_id, trial_index=trial, trial_seed=seed,
        episode_attempt=attempt,
    )
    store = EpisodeLifecycleStore(settings.trace_root, identity, max_unsafe_manual_resumes=2)
    opened = store.open()
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id=f"g-{task_id}-{attempt}",
                                   turn_index=attempt, episode_attempt=attempt)
    # Three sidecar rows are evidence for one failed *episode attempt*.
    for provider_attempt in range(1, 4):
        sidecar.record(RetryAttempt(
            provider_attempt, ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE,
            provider_attempt < 3, 1.0 if provider_attempt == 1 else 2.0 if provider_attempt == 2 else 0.0,
        ))
    store.terminalize(opened, status=LifecycleStatus.FAILED_OPERATIONAL,
                      outcome="failed", error="exhausted", side_effect=False)


def test_v3_terminalization_is_idempotent_and_counts_episode_attempts(tmp_path):
    settings = A1RunSettings.create(
        trace_root=tmp_path / "traces", domain="mock", task_ids=["78"],
        agent="llm_agent", user="user_simulator", llm_agent="agent", llm_user="user",
        num_trials=1, trial_seeds=[626729], max_steps=5, max_errors=2,
        max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={},
        a2_retry=True, a2_max_operational_failures=3,
        a2_safe_operational_failure_scope="per_logical_episode",
        a2_max_unsafe_manual_resumes=2,
    )
    settings.write_or_validate_config()
    frozen = json.loads(settings.config_path.read_text(encoding="utf-8"))
    assert frozen["safe_operational_failure_scope"] == "per_logical_episode"
    assert frozen["max_operational_failures"] == 3
    for attempt in (1, 2, 3):
        _safe_failed_attempt_for(settings, task_id="78", trial=0, seed=626729, attempt=attempt)

    first = terminalize_safe_failure_budget(
        settings, task_id="78", trial_index=0, trial_seed=626729,
        max_safe_failures_per_logical_episode=3,
    )
    second = terminalize_safe_failure_budget(
        settings, task_id="78", trial_index=0, trial_seed=626729,
        max_safe_failures_per_logical_episode=3,
    )
    assert first == second
    assert first["safe_failure_count"] == 3
    assert first["scope"] == "per_logical_episode"
    audit = (settings.trace_root / "manual-safe-unresolved-audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 1
    assert not list(settings.trace_root.glob("*attempt-004*"))


def test_v3_safe_exhausted_tuple_skips_but_sibling_continues(tmp_path, monkeypatch):
    """The v3 scheduler terminalizes 78:0 without stopping independent 79:0."""
    base = load_tasks("mock")[0]
    exhausted = base.model_copy(update={"id": "78"})
    sibling = base.model_copy(update={"id": "79"})
    random.seed(300)
    trial_seed = random.randint(0, 1000000)
    trace, save = tmp_path / "traces", tmp_path / "results.json"
    settings = A1RunSettings.create(
        trace_root=trace, domain="mock", task_ids=["78", "79"], agent="llm_agent",
        user="user_simulator", llm_agent="agent", llm_user="user", num_trials=1,
        trial_seeds=[trial_seed], max_steps=5, max_errors=2, max_concurrency=1,
        root_seed=300, llm_args_agent={}, llm_args_user={}, a2_retry=True,
        a2_max_operational_failures=3,
        a2_safe_operational_failure_scope="per_logical_episode",
        a2_max_unsafe_manual_resumes=2,
    )
    settings.write_or_validate_config()
    for attempt in (1, 2, 3):
        _safe_failed_attempt_for(settings, task_id="78", trial=0, seed=trial_seed, attempt=attempt)

    calls = []
    def fake_run_task(**kwargs):
        calls.append((str(kwargs["task"].id), kwargs["trial_index"]))
        return SimulationRun(
            id="79-0", task_id=str(kwargs["task"].id), start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:00:01", duration=1.0,
            termination_reason=TerminationReason.AGENT_STOP, messages=[], seed=kwargs["seed"],
        )
    monkeypatch.setattr(run_module, "run_task", fake_run_task)
    monkeypatch.setattr(run_module, "validate_a2_episode_before_results_commit", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_module, "finalize_episode_after_results_commit", lambda *args, **kwargs: None)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")

    result = run_tasks(
        domain="mock", tasks=[exhausted, sibling], agent="llm_agent", user="user_simulator",
        llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
        num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
        max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
        a2_retry=True, a2_max_operational_failures=3,
        a2_safe_operational_failure_scope="per_logical_episode",
    )
    assert calls == [("79", 0)]
    assert [row.task_id for row in result.simulations] == ["79"]
    assert not list(trace.glob("task-78__trial-000__seed-*__attempt-004*"))
    accounting = json.loads((trace / "a2-operational-accounting.json").read_text())
    assert accounting["benchmark_completed"] == 1
    assert accounting["operationally_unresolved"] == [{
        "task_id": "78", "trial": 0, "seed": trial_seed,
        "latest_attempt": 3, "reason": "safe_failure_budget_exhausted",
    }]


def test_v3_safe_terminalizer_refuses_post_write_failure(tmp_path):
    settings = _settings(tmp_path)
    identity = EpisodeIdentity(run_id=settings.run_id, task_id="78", trial_index=0,
                               trial_seed=626729, episode_attempt=1)
    store = EpisodeLifecycleStore(settings.trace_root, identity, max_unsafe_manual_resumes=2)
    opened = store.open()
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id="post-write",
                                   turn_index=1, episode_attempt=1)
    for provider_attempt in range(1, 4):
        sidecar.record(RetryAttempt(provider_attempt, ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE,
                                    provider_attempt < 3, 0.0))
    store.terminalize(opened, status=LifecycleStatus.FAILED_OPERATIONAL,
                      outcome="failed", error="exhausted", side_effect=True)
    with pytest.raises(ValueError, match="exhausted per-episode"):
        terminalize_safe_failure_budget(
            settings, task_id="78", trial_index=0, trial_seed=626729,
            max_safe_failures_per_logical_episode=3,
        )
    assert not (settings.trace_root / "manual-safe-unresolved-audit.jsonl").exists()


def test_safe_failure_budget_is_terminalized_per_exact_logical_episode(tmp_path):
    settings = _settings(tmp_path)
    for attempt in (1, 2, 3):
        _safe_failed_attempt(settings, attempt)

    with pytest.raises(ValueError, match="latest failed attempt"):
        declare_safe_failure_unresolved(
            settings, "78:0:2", scheduled_task_ids={"78", "79"},
            max_safe_failures_per_logical_episode=3,
        )

    assert declare_safe_failure_unresolved(
        settings, "78:0:3", scheduled_task_ids={"78", "79"},
        max_safe_failures_per_logical_episode=3,
    ) == ("78", 0)
    amendment = json.loads((settings.trace_root / "a2-safe-failure-protocol-amendment-v1.json").read_text())
    assert amendment["scope"] == "per_logical_episode"
    assert amendment["max_safe_failures_per_logical_episode"] == 3
    audit = [json.loads(line) for line in (settings.trace_root / "manual-safe-unresolved-audit.jsonl").read_text().splitlines()]
    assert audit[0]["declaration"] == "78:0:3"
    assert audit[0]["safe_failure_count"] == 3

    lifecycle, reason = validate_a2_unresolved_episode_skip(
        settings, task_id="78", trial_index=0, trial_seed=626729,
        max_unsafe_manual_resumes=2,
    )
    assert lifecycle.identity.episode_attempt == 3
    assert reason == "safe_failure_budget_exhausted"
    assert not list(settings.trace_root.glob("*attempt-004*"))

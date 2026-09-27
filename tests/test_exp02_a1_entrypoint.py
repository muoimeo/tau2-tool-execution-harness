"""EXP-02 entrypoint tests: local fixtures only, no provider/gateway traffic."""

from datetime import datetime

import pytest

from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.entrypoint import (
    A1EntrypointError,
    A1RunSettings,
    create_episode_harness,
    default_trace_root,
    validate_closed_episode_skip,
)
from tau2.harness.episode import ClosedEpisodeSkip
from tau2.harness.integration_contracts import LifecycleStatus
from tau2.run import load_tasks, run_tasks


class EntryToolkit(ToolKitBase):
    def __init__(self):
        super().__init__(db={"writes": 0})

    @is_tool(ToolType.READ)
    def lookup(self, key: str) -> str:
        return key


def settings(tmp_path, *, max_concurrency=1, task_ids=("71",)):
    return A1RunSettings.create(
        trace_root=tmp_path / "traces",
        domain="mock",
        task_ids=task_ids,
        agent="llm_agent",
        user="user_simulator",
        llm_agent="agent-model",
        llm_user="user-model",
        num_trials=1,
        trial_seeds=(626729,),
        max_steps=200,
        max_errors=10,
        max_concurrency=max_concurrency,
        root_seed=300,
        llm_args_agent={"temperature": 0.0},
        llm_args_user={"temperature": 0.0},
    )


def environment():
    return Environment(domain_name="test", policy="policy", tools=EntryToolkit())


def test_a1_settings_are_explicit_deterministic_and_fail_closed_on_config_mismatch(tmp_path):
    first = settings(tmp_path)
    first.write_or_validate_config()
    first.write_or_validate_config()
    assert first.config_path.name == "a1-run-config.json"
    assert first.identity(task_id="71", trial_index=0, trial_seed=626729).filename_stem() == (
        f"task-71__trial-000__seed-626729__attempt-001"
    )
    assert default_trace_root(save_to=tmp_path / "results.json") == tmp_path / "results.a1-traces"

    changed = settings(tmp_path, task_ids=("72",))
    with pytest.raises(A1EntrypointError, match="different frozen run configuration"):
        changed.write_or_validate_config()
    with pytest.raises(A1EntrypointError, match="max_concurrency=1"):
        settings(tmp_path / "other", max_concurrency=2)


def test_episode_resume_is_closed_skip_or_fresh_attempt_with_isolated_environment(tmp_path):
    configured = settings(tmp_path)
    configured.write_or_validate_config()
    first = create_episode_harness(
        settings=configured, task_id="71", trial_index=0, trial_seed=626729,
        environment_factory=environment,
    )
    first.environment.tools.db["writes"] = 5

    rerun = create_episode_harness(
        settings=configured, task_id="71", trial_index=0, trial_seed=626729,
        environment_factory=environment,
    )
    assert first.lifecycle_store.read().status == LifecycleStatus.ABORTED
    assert rerun.identity.episode_attempt == 2
    assert rerun.environment.tools.db == {"writes": 0}
    rerun.trace_logger.preflight()
    rerun.lifecycle_store.close()
    rerun.close_resources()

    skipped = create_episode_harness(
        settings=configured, task_id="71", trial_index=0, trial_seed=626729,
        environment_factory=lambda: (_ for _ in ()).throw(AssertionError("closed trace must not build environment")),
    )
    assert isinstance(skipped, ClosedEpisodeSkip)
    assert validate_closed_episode_skip(configured, rerun.identity) is True


def test_run_tasks_keeps_harness_off_by_default_and_propagates_it_only_when_explicit(tmp_path, monkeypatch):
    task = load_tasks("mock")[0]
    captured = []

    def fake_run_task(**kwargs):
        captured.append(kwargs.get("a1_settings"))
        now = datetime.now().isoformat()
        return SimulationRun(
            id="local", task_id=kwargs["task"].id, start_time=now, end_time=now,
            duration=0.0, termination_reason=TerminationReason.AGENT_STOP, messages=[], seed=kwargs["seed"],
        )

    monkeypatch.setattr("tau2.run.run_task", fake_run_task)
    common = dict(
        domain="mock", tasks=[task], agent="llm_agent", user="user_simulator",
        llm_agent="agent-model", llm_args_agent={}, llm_user="user-model", llm_args_user={},
        num_trials=1, max_steps=10, max_errors=2, console_display=False,
        max_concurrency=1, seed=300,
    )
    run_tasks(**common)
    assert captured.pop() is None

    finalized = []
    monkeypatch.setattr(
        "tau2.run.finalize_episode_after_results_commit",
        lambda settings, identity: finalized.append((settings, identity)),
    )
    run_tasks(**common, harness_enabled=True, save_to=tmp_path / "a1-results.json")
    enabled = captured.pop()
    assert enabled is not None
    assert enabled.config_path.exists()
    assert enabled.trace_root == tmp_path / "a1-results.a1-traces"
    assert len(finalized) == 1

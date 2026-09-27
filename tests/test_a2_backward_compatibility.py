"""Gate 4: A2 opt-in compatibility with committed A0/A1 artifacts."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from tau2.data_model.simulation import Results, RunConfig, SimulationRun
from tau2.harness.integration_contracts import EpisodeLifecycle
from tau2.run import load_tasks, run_tasks
from tau2.utils import llm_utils


REPO_ROOT = Path(__file__).resolve().parents[1]
A0_RESULTS = REPO_ROOT / "data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json"
A1_RESULTS = REPO_ROOT / "data/simulations/30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/results.json"
A1_LEGACY_LIFECYCLE = REPO_ROOT / "data/simulations/30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/traces/task-101__trial-000__seed-626729__attempt-001.lifecycle.json"


def _response(content: str):
    choice = SimpleNamespace(
        finish_reason="stop",
        message=SimpleNamespace(role="assistant", content=content, tool_calls=[]),
    )
    choice.to_dict = lambda: {}
    return SimpleNamespace(choices=[choice], model="mock", to_dict=lambda: {})


def test_committed_a0_and_a1_results_load_without_modifying_raw_bytes():
    """Read committed benchmark artifacts directly; never migrate or rewrite them."""
    for path in (A0_RESULTS, A1_RESULTS):
        assert path.is_file(), f"required compatibility artifact missing: {path}"
        before = path.read_bytes()
        parsed = Results.model_validate_json(before)
        raw = json.loads(before)

        assert len(parsed.simulations) == 232
        assert set(raw) == {"timestamp", "info", "tasks", "simulations"}
        assert "a2_retry" not in raw
        assert "a2_retry" not in raw["info"]
        assert path.read_bytes() == before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()


def test_legacy_a1_lifecycle_without_provider_fields_loads_unchanged():
    """Provider-sidecar fields are optional for A1 lifecycle artifacts made pre-A2."""
    assert A1_LEGACY_LIFECYCLE.is_file()
    before = A1_LEGACY_LIFECYCLE.read_bytes()
    raw = json.loads(before)
    parsed = EpisodeLifecycle.model_validate_json(before)

    assert "provider_retry_filename" not in raw
    assert parsed.provider_retry_filename is None
    assert parsed.provider_retry_event_count is None
    assert parsed.provider_retry_last_sequence is None
    assert parsed.provider_retry_sha256 is None
    assert A1_LEGACY_LIFECYCLE.read_bytes() == before


def test_old_run_config_without_a2_fields_keeps_backward_compatible_defaults():
    legacy = RunConfig(domain="mock").model_dump()
    legacy.pop("a2_retry")
    legacy.pop("a2_max_operational_failures")
    legacy.pop("a2_max_unsafe_manual_resumes")
    legacy.pop("a2_authorize_unsafe_resume")
    parsed = RunConfig.model_validate(legacy)

    assert parsed.a2_retry is False
    assert parsed.a2_max_unsafe_manual_resumes == 2


def test_simulation_run_schema_matches_committed_a0_and_a1_records():
    expected = {
        "id", "task_id", "timestamp", "start_time", "end_time", "duration",
        "termination_reason", "agent_cost", "user_cost", "reward_info", "messages",
        "trial", "seed",
    }
    assert set(SimulationRun.model_fields) == expected
    for path in (A0_RESULTS, A1_RESULTS):
        first_simulation = json.loads(path.read_bytes())["simulations"][0]
        assert set(first_simulation) == expected
        assert "a2_retry" not in first_simulation


def test_a2_off_keeps_native_retry_and_raw_results_schema(tmp_path, monkeypatch):
    calls, user_calls = [], [0]

    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 2 else "please help")
        return _response("I can help with that.")

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    save = tmp_path / "a2-off-results.json"
    run_tasks(
        domain="mock", tasks=[load_tasks("mock")[0]], agent="llm_agent",
        user="user_simulator", llm_agent="agent", llm_args_agent={},
        llm_user="user", llm_args_user={}, num_trials=1, max_steps=5,
        max_errors=2, save_to=save, console_display=False, max_concurrency=1,
        seed=300, harness_enabled=False, a2_retry=False,
    )

    agent_calls = [call for call in calls if call["model"] == "agent"]
    assert agent_calls and all(call["num_retries"] == 3 for call in agent_calls)
    raw = json.loads(save.read_bytes())
    assert len(raw["simulations"]) == 1
    assert "a2_retry" not in raw
    assert "a2_retry" not in raw["info"]
    assert "a2_retry" not in raw["simulations"][0]
    assert set(raw["simulations"][0]) == set(SimulationRun.model_fields)

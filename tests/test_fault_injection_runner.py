import json
from pathlib import Path

import litellm
import pytest

from tau2.fault_injection.contracts import FaultKind, FaultScenario
from tau2.fault_injection.fixtures import compile_fixture
from tau2.fault_injection.runner import execute_scenario, write_scenario_plan
from tau2.utils import llm_utils


A0_RESULTS = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")


@pytest.mark.offline_deterministic
def test_clean_fixture_runs_through_run_tasks_with_loopback_provider(tmp_path, monkeypatch):
    """Production run_tasks -> Orchestrator -> generate -> LiteLLM -> loopback."""
    monkeypatch.setattr(llm_utils, "completion", litellm.completion)
    fixture = compile_fixture(
        A0_RESULTS,
        task_id="68",
        trial=0,
        agent_model="openai/claude-sonnet-5",
        user_model="openai/claude-opus-4-8",
    )
    scenario = FaultScenario(
        scenario_id="c0-task68-a0",
        fault=FaultKind.CLEAN_REPLAY,
        fixture_id=fixture.fixture_id,
        track="a0-native",
    )
    write_scenario_plan(tmp_path, scenario, fixture)
    observation = execute_scenario(tmp_path, scenario, fixture)
    output = tmp_path / "a0-native" / scenario.scenario_id
    assert observation.disposition == "serialized"
    assert observation.dispatch_count > 0
    assert observation.provider_attempt_count > 0
    assert (output / "results.json").exists()
    payload = json.loads((output / "scenario-observation.json").read_text(encoding="utf-8"))
    assert payload["native_mutation_count"] == 0
    assert payload["provider_request_shapes"]
    assert "fixture input" not in (output / "scenario-observation.json").read_text(encoding="utf-8")


@pytest.mark.offline_deterministic
def test_plan_refuses_overwrite(tmp_path):
    fixture = compile_fixture(
        A0_RESULTS,
        task_id="68",
        trial=0,
        agent_model="openai/claude-sonnet-5",
        user_model="openai/claude-opus-4-8",
    )
    scenario = FaultScenario(scenario_id="c0", fault=FaultKind.CLEAN_REPLAY, fixture_id=fixture.fixture_id, track="a0-native")
    write_scenario_plan(tmp_path, scenario, fixture)
    with pytest.raises(FileExistsError):
        write_scenario_plan(tmp_path, scenario, fixture)


@pytest.mark.offline_deterministic
def test_a1_provider_encoded_missing_arg_rejects_before_native_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_utils, "completion", litellm.completion)
    fixture = compile_fixture(A0_RESULTS, task_id="68", trial=0,
                              agent_model="openai/claude-sonnet-5", user_model="openai/claude-opus-4-8")
    scenario = FaultScenario(scenario_id="p4-task68-a1", fault=FaultKind.MISSING_REQUIRED_ARGUMENT,
                             fixture_id=fixture.fixture_id, track="a1-validation", target_ordinal=2)
    write_scenario_plan(tmp_path, scenario, fixture)
    observation = execute_scenario(tmp_path, scenario, fixture)
    assert observation.disposition == "serialized"
    trace = next((tmp_path / "a1-validation" / scenario.scenario_id / "traces").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "SCHEMA_MISSING_REQUIRED_ARG" in trace
    assert observation.dispatch_count > 0  # remaining clean fixture calls execute
    faulty_call = [json.loads(row) for row in trace.splitlines() if "SCHEMA_MISSING_REQUIRED_ARG" in row]
    assert faulty_call and all(row["phase"] != "dispatch_completed" for row in faulty_call)


@pytest.mark.offline_deterministic
def test_a2_http_503_then_original_action_recovers_on_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_utils, "completion", litellm.completion)
    fixture = compile_fixture(A0_RESULTS, task_id="68", trial=0,
                              agent_model="openai/claude-sonnet-5", user_model="openai/claude-opus-4-8")
    scenario = FaultScenario(scenario_id="r2-task68-a2", fault=FaultKind.HTTP_503_ONCE,
                             fixture_id=fixture.fixture_id, track="a2-retry", target_ordinal=0)
    write_scenario_plan(tmp_path, scenario, fixture)
    observation = execute_scenario(tmp_path, scenario, fixture)
    assert observation.disposition == "serialized"
    assert observation.recovery_outcome == "provider_retry_recovered"
    sidecar = next((tmp_path / "a2-retry" / scenario.scenario_id / "traces").glob("*.provider-retry.jsonl"))
    events = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    assert any(event["failure_code"] == "PROVIDER_HTTP_503" for event in events)
    assert any(event["outcome"] == "success" for event in events)

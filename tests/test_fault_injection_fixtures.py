import json
from pathlib import Path

import pytest

from tau2.fault_injection.fixtures import FixtureCompilationError, compile_fixture, write_fixture


A0_RESULTS = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")


@pytest.mark.offline_deterministic
def test_compile_fixture_removes_source_raw_data_and_is_deterministic(tmp_path):
    fixture = compile_fixture(
        A0_RESULTS,
        task_id="68",
        trial=0,
        agent_model="openai/claude-sonnet-5",
        user_model="openai/claude-opus-4-8",
    )

    assert fixture.fixture_id == "retail-task-68-trial-0"
    assert fixture.agent_actions and fixture.user_actions
    serialized = fixture.model_dump_json()
    assert "raw_data" not in serialized
    assert "reasoning_content" not in serialized
    target = write_fixture(fixture, tmp_path / "fixture.json")
    assert json.loads(target.read_text(encoding="utf-8"))["fixture_sha256"] == fixture.fixture_sha256
    with pytest.raises(FileExistsError):
        write_fixture(fixture, target)


@pytest.mark.offline_deterministic
def test_compile_fixture_requires_one_source_simulation():
    with pytest.raises(FixtureCompilationError):
        compile_fixture(
            A0_RESULTS,
            task_id="does-not-exist",
            trial=0,
            agent_model="openai/claude-sonnet-5",
            user_model="openai/claude-opus-4-8",
        )

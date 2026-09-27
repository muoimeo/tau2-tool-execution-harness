from pathlib import Path

import pytest

from tau2.fault_injection.contracts import FaultKind
from tau2.fault_injection.fixtures import compile_fixture
from tau2.fault_injection.plans import build_fi_v1_schedule


A0_RESULTS = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")


@pytest.mark.offline_deterministic
def test_fi_v1_schedule_has_one_shared_fixture_per_fault_across_three_tracks():
    fixtures = {
        task_id: compile_fixture(A0_RESULTS, task_id=task_id, trial=trial,
                                 agent_model="openai/claude-sonnet-5", user_model="openai/claude-opus-4-8")
        for task_id, trial in (("68", 0), ("70", 0), ("73", 0), ("37", 1), ("79", 3))
    }
    schedule = build_fi_v1_schedule(fixtures)
    assert len(schedule) == 42
    d1 = [item for item in schedule if item.fault == FaultKind.DOMAIN_CONSTRAINT]
    assert {item.track for item in d1} == {"a0-native", "a1-validation", "a2-retry"}
    assert {item.fixture_id for item in d1} == {fixtures["79"].fixture_id}
    s1 = [item for item in schedule if item.fault == FaultKind.PROVIDER_EXHAUST_POST_WRITE]
    assert all(item.target_ordinal > 0 for item in s1)

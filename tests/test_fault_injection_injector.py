import pytest

from tau2.fault_injection.contracts import CompiledFixture, FaultKind, FaultScenario, ProviderAction
from tau2.fault_injection.injector import InjectionError, apply_fault


def _fixture() -> CompiledFixture:
    return CompiledFixture(
        fixture_id="fixture-1",
        task_id="68",
        trial=0,
        seed=1,
        source_results_path="source.json",
        source_results_sha256="a" * 64,
        agent_model="openai/agent",
        user_model="openai/user",
        agent_actions=[ProviderAction.response_action({"role": "assistant", "content": "clean"})],
        user_actions=[ProviderAction.response_action({"role": "assistant", "content": "user"})],
        fixture_sha256="b" * 64,
    )


@pytest.mark.offline_deterministic
@pytest.mark.parametrize("fault", [
    FaultKind.MALFORMED_JSON,
    FaultKind.JSON_NON_OBJECT,
    FaultKind.UNKNOWN_TOOL,
    FaultKind.MISSING_REQUIRED_ARGUMENT,
    FaultKind.EXTRA_ARGUMENT,
    FaultKind.WRONG_STRICT_TYPE,
    FaultKind.DOMAIN_CONSTRAINT,
    FaultKind.NATIVE_PRECONDITION,
])
def test_structural_faults_are_encoded_at_provider_boundary(fault):
    fixture = _fixture()
    injected = apply_fault(fixture, FaultScenario(scenario_id="s", fault=fault, fixture_id=fixture.fixture_id, track="a1-validation"))
    payload = injected.agent_actions[0].response
    assert payload is not None
    call = payload["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["arguments"]
    assert fixture.agent_actions[0] != injected.agent_actions[0]


@pytest.mark.offline_deterministic
def test_retry_fault_is_a_transport_or_shape_outcome_not_prompt_mutation():
    fixture = _fixture()
    injected = apply_fault(fixture, FaultScenario(scenario_id="s", fault=FaultKind.HTTP_503_ONCE, fixture_id=fixture.fixture_id, track="a2-retry"))
    assert injected.agent_actions[0].http_status == 503
    assert injected.agent_actions[0].response is None


@pytest.mark.offline_deterministic
def test_timeout_fault_exceeds_the_fi_client_timeout():
    fixture = _fixture()
    injected = apply_fault(fixture, FaultScenario(
        scenario_id="s", fault=FaultKind.TIMEOUT_ONCE,
        fixture_id=fixture.fixture_id, track="a2-retry",
    ))
    assert injected.agent_actions[0].sleep_seconds is not None
    assert injected.agent_actions[0].sleep_seconds > 2.0


@pytest.mark.offline_deterministic
def test_exhaustion_fault_encodes_exactly_three_provider_failures():
    fixture = _fixture()
    injected = apply_fault(fixture, FaultScenario(
        scenario_id="s", fault=FaultKind.PROVIDER_EXHAUST_PRE_WRITE,
        fixture_id=fixture.fixture_id, track="a2-retry",
    ))
    assert len(injected.agent_actions) == 3
    assert all(action.http_status == 503 for action in injected.agent_actions)


@pytest.mark.offline_deterministic
def test_injector_rejects_unrelated_fixture():
    with pytest.raises(InjectionError):
        apply_fault(_fixture(), FaultScenario(scenario_id="s", fault=FaultKind.CLEAN_REPLAY, fixture_id="other", track="a0-native"))

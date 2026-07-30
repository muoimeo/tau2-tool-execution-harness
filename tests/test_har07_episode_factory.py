"""HAR-07A deterministic episode factory and lifecycle binding tests."""

from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.contracts import Requestor
from tau2.harness.episode import ClosedEpisodeSkip, EpisodeHarnessFactory
from tau2.harness.integration_contracts import EpisodeIdentity, LifecycleStatus


class AgentToolkit(ToolKitBase):
    def __init__(self):
        super().__init__(db={"writes": 0})

    @is_tool(ToolType.READ)
    def lookup(self, key: str) -> str:
        """Read an item."""
        return key

    @is_tool(ToolType.WRITE)
    def increment(self, amount: int) -> int:
        """Increment the isolated test DB."""
        self.db["writes"] += amount
        return self.db["writes"]


class UserToolkit(ToolKitBase):
    @is_tool(ToolType.READ)
    def identify(self, email: str) -> str:
        """Identify the user."""
        return email


def identity(attempt: int = 1) -> EpisodeIdentity:
    return EpisodeIdentity(
        run_id="a1-test", task_id="71", trial_index=2,
        trial_seed=300, episode_attempt=attempt,
    )


def test_factory_binds_all_components_once_per_fresh_episode(tmp_path):
    environments = []

    def make_environment():
        environment = Environment(
            domain_name="test", policy="policy", tools=AgentToolkit(), user_tools=UserToolkit()
        )
        environments.append(environment)
        return environment

    harness = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment).create(identity())
    assert harness.identity == identity()
    assert harness.lifecycle.status == LifecycleStatus.OPEN
    assert harness.lifecycle_store.lifecycle_path.exists()
    assert len(harness.registry_snapshot.entries) == 3
    assert set(harness.executors) == {
        (Requestor.ASSISTANT, "lookup"),
        (Requestor.ASSISTANT, "increment"),
        (Requestor.USER, "identify"),
    }
    assert harness.executor_for(requestor=Requestor.ASSISTANT, tool_name="lookup")
    assert all(
        executor._native_executor.duplicate_guard is harness.duplicate_guard
        for executor in harness.executors.values()
    )
    assert harness.context_for_turn(turn_index=4, requestor=Requestor.ASSISTANT).trial_seed == 300
    assert harness.parser_failure_sidecar(turn_index=4, requestor=Requestor.ASSISTANT) is harness.parser_failure_sidecar(
        turn_index=4, requestor=Requestor.ASSISTANT
    )
    assert len(environments) == 1


def test_incomplete_attempt_is_aborted_and_rerun_uses_clean_environment_and_new_attempt(tmp_path):
    environments = []

    def make_environment():
        environment = Environment(domain_name="test", policy="policy", tools=AgentToolkit())
        environments.append(environment)
        return environment

    factory = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment)
    first = factory.create(identity())
    first.environment.tools.increment(9)
    first_sidecar = first.parser_failure_sidecar(turn_index=1, requestor=Requestor.ASSISTANT)

    rerun = factory.create(identity())
    assert rerun.identity.episode_attempt == 2
    assert first.lifecycle_store.read().status == LifecycleStatus.ABORTED
    assert rerun.environment is not first.environment
    assert rerun.environment.tools.db == {"writes": 0}
    assert rerun.duplicate_guard is not first.duplicate_guard
    assert rerun.executor_for(requestor=Requestor.ASSISTANT, tool_name="increment") is not first.executor_for(
        requestor=Requestor.ASSISTANT, tool_name="increment"
    )
    assert rerun.params_models is not first.params_models
    assert rerun.parser_failure_sidecar(turn_index=1, requestor=Requestor.ASSISTANT) is not first_sidecar
    assert len(environments) == 2


def test_verified_closed_attempt_is_skipped_without_constructing_environment(tmp_path):
    calls = 0

    def make_environment():
        nonlocal calls
        calls += 1
        return Environment(domain_name="test", policy="policy", tools=AgentToolkit())

    factory = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment)
    started = factory.create(identity())
    started.lifecycle_store.trace_path.write_bytes(b'{"trace":"complete"}\n')
    started.lifecycle_store.close()

    skipped = factory.create(identity())
    assert isinstance(skipped, ClosedEpisodeSkip)
    assert skipped.lifecycle.status == LifecycleStatus.CLOSED
    assert calls == 1

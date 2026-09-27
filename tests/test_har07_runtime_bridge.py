"""HAR-07C deterministic runtime bridge tests; no gateway or live episode."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.contracts import Requestor, TrustedRouteContext
from tau2.harness.dispatcher import DuplicateExecutionError
from tau2.harness.episode import EpisodeHarnessFactory
from tau2.harness.integration_contracts import EpisodeIdentity
from tau2.harness.parser import ParserNormalizer, RawToolCall
from tau2.harness.runtime import HarnessRuntimeBridge
from tau2.orchestrator.orchestrator import Orchestrator, Role

NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class RuntimeToolkit(ToolKitBase):
    def __init__(self):
        super().__init__(db={"writes": 0, "calls": []})

    @is_tool(ToolType.READ)
    def lookup(self, key: str) -> dict:
        """Read the supplied key."""
        self.db["calls"].append({"tool": "lookup", "key": key})
        return {"key": key}

    @is_tool(ToolType.WRITE)
    def increment(self, amount: int) -> int:
        """Mutate the isolated DB."""
        self.db["calls"].append({"tool": "increment", "amount": amount})
        self.db["writes"] += amount
        return self.db["writes"]

    @is_tool(ToolType.WRITE)
    def modify_pending_order_items(self, item_ids: list[str], new_item_ids: list[str]) -> str:
        """Test only the one approved HAR-04 domain constraint."""
        self.db["calls"].append({"tool": "modify", "item_ids": item_ids, "new_item_ids": new_item_ids})
        return "modified"

    @is_tool(ToolType.READ)
    def explode(self) -> str:
        """Raise a native exception."""
        raise ValueError("secret a@example.com #W1234567")


def identity() -> EpisodeIdentity:
    return EpisodeIdentity(run_id="a1", task_id="71", trial_index=0, trial_seed=300, episode_attempt=1)


def make_harness(tmp_path):
    environments = []

    def make_environment():
        env = Environment(domain_name="test", policy="policy", tools=RuntimeToolkit())
        environments.append(env)
        return env

    harness = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment).create(identity())
    return harness, environments[0]


def context(harness, turn=4):
    return harness.context_for_turn(turn_index=turn, requestor=Requestor.ASSISTANT)


def parser_failure(harness, turn=4):
    ctx = context(harness, turn)
    batch = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="bad", raw_tool_name="lookup", raw_arguments_text="{"),),
        run_id=ctx.run_id, task_id=ctx.task_id, trial_index=ctx.trial_index,
        trial_seed=ctx.trial_seed, turn_index=ctx.turn_index,
        trusted_route=TrustedRouteContext(route_id="orchestrated-llm-agent", requestor=Requestor.ASSISTANT),
        parsed_at=NOW,
    )
    return ctx, harness.parser_failure_sidecar(turn_index=turn, requestor=Requestor.ASSISTANT).register(batch.failures[0])


def test_parser_enabled_without_execution_bridge_fails_before_episode(tmp_path):
    harness, environment = make_harness(tmp_path)
    agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    ctx = context(harness)
    agent.enable_harness_parser(
        context_supplier=lambda: ctx,
        sidecar_supplier=lambda value: harness.parser_failure_sidecar(turn_index=value.turn_index, requestor=value.requestor),
    )
    with pytest.raises(ValueError, match="no execution bridge"):
        Orchestrator("test", agent, SimpleNamespace(), environment, SimpleNamespace(id="71"), seed=300)


def test_parser_failure_consumes_sidecar_and_never_calls_environment(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    ctx, synthetic = parser_failure(harness)
    bridge = HarnessRuntimeBridge(harness)
    invoked = False

    def unexpected(_call):
        nonlocal invoked
        invoked = True
        raise AssertionError("synthetic parser rejection must not reach Environment")

    monkeypatch.setattr(environment, "get_response", unexpected)
    message = bridge.execute_assistant_call(synthetic, context=ctx)
    assert invoked is False
    assert message.id == synthetic.id and message.error is True
    assert environment.tools.db == {"writes": 0, "calls": []}
    with pytest.raises(RuntimeError, match="already consumed"):
        bridge.execute_assistant_call(synthetic, context=ctx)


@pytest.mark.parametrize("call", [
    ToolCall(id="unknown", name="not_registered", arguments={}),
    ToolCall(id="wrong-type", name="increment", arguments={"amount": "2"}),
    ToolCall(id="domain", name="modify_pending_order_items", arguments={"item_ids": ["a"], "new_item_ids": ["a"]}),
])
def test_registry_schema_and_domain_rejections_do_not_invoke_or_mutate(tmp_path, call):
    harness, environment = make_harness(tmp_path)
    before = {key: list(value) if isinstance(value, list) else value for key, value in environment.tools.db.items()}
    message = HarnessRuntimeBridge(harness).execute_assistant_call(call, context=context(harness))
    assert message.error is True
    assert environment.tools.db == before


def test_frozen_domain_reject_is_native_equivalent_but_never_dispatches_and_replays(tmp_path):
    class NativeEquivalentToolkit(ToolKitBase):
        def __init__(self):
            super().__init__(db={"invocations": 0})

        @is_tool(ToolType.WRITE)
        def modify_pending_order_items(self, item_ids: list[str], new_item_ids: list[str]) -> str:
            self.db["invocations"] += 1
            if any(old == new for old, new in zip(item_ids, new_item_ids)):
                raise ValueError("The new item id should be different from the old item id")
            return "modified"

    def make_environment():
        return Environment(domain_name="test", policy="policy", tools=NativeEquivalentToolkit())

    harness = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment).create(identity())
    call = ToolCall(
        id="same-item", name="modify_pending_order_items",
        arguments={"item_ids": ["old"], "new_item_ids": ["old"]},
    )
    enabled = HarnessRuntimeBridge(harness).execute_assistant_call(call, context=context(harness))

    assert enabled.error is True
    assert enabled.content == "Error: The new item id should be different from the old item id"
    assert harness.environment.tools.db["invocations"] == 0
    import json
    trace = [json.loads(line) for line in harness.lifecycle_store.trace_path.read_text(encoding="utf-8").splitlines()]
    assert trace[-1]["origin"] == "harness_reject"
    assert trace[-1]["dispatch_count"] == 0

    replay_environment = make_environment()
    replay_environment.set_state(
        initialization_data=None,
        initialization_actions=None,
        message_history=[AssistantMessage(role="assistant", tool_calls=[call]), enabled],
    )
    # Replay dispatches natively; this asserts message compatibility only.
    assert replay_environment.tools.db["invocations"] == 1


def test_allow_read_write_native_exception_and_duplicate_guard(tmp_path):
    harness, environment = make_harness(tmp_path)
    bridge = HarnessRuntimeBridge(harness)
    ctx = context(harness)
    read_call = ToolCall(id="read", name="lookup", arguments={"key": "x"})
    write_call = ToolCall(id="write", name="increment", arguments={"amount": 3})
    read = bridge.execute_assistant_call(read_call, context=ctx)
    write = bridge.execute_assistant_call(write_call, context=ctx)
    failure = bridge.execute_assistant_call(ToolCall(id="boom", name="explode", arguments={}), context=ctx)
    assert read.error is False and read.content == '{"key": "x"}'
    assert write.error is False and write.content == '"3"'
    assert failure.error is True and failure.content == "Tool execution failed."
    assert environment.tools.db["writes"] == 3
    assert read_call.arguments == {"key": "x"} and write_call.arguments == {"amount": 3}
    with pytest.raises(DuplicateExecutionError):
        bridge.execute_assistant_call(ToolCall(id="write", name="increment", arguments={"amount": 3}), context=ctx)
    assert environment.tools.db["writes"] == 3


def test_safe_native_domain_error_matches_native_environment_tool_message(tmp_path):
    class MissingUserToolkit(ToolKitBase):
        @is_tool(ToolType.READ)
        def lookup_user(self, zip: str) -> str:
            raise ValueError("User not found")

    native_environment = Environment(domain_name="test", policy="policy", tools=MissingUserToolkit())
    native = native_environment.get_response(
        ToolCall(id="missing", name="lookup_user", arguments={"zip": "98178"})
    )
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path,
        environment_factory=lambda: Environment(domain_name="test", policy="policy", tools=MissingUserToolkit()),
    ).create(identity())
    enabled = HarnessRuntimeBridge(harness).execute_assistant_call(
        ToolCall(id="missing", name="lookup_user", arguments={"zip": "98178"}),
        context=context(harness),
    )
    assert native == enabled


def test_orchestrator_preserves_mixed_provider_order_and_never_falls_through_to_native_environment(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    orchestrator = Orchestrator("test", agent, SimpleNamespace(), environment, SimpleNamespace(id="71"), seed=300, harness=harness)
    ctx = context(harness, turn=0)
    sidecar = harness.parser_failure_sidecar(turn_index=0, requestor=Requestor.ASSISTANT)
    malformed = ParserNormalizer().parse(
        (
            RawToolCall(provider_call_id="bad-1", raw_tool_name="lookup", raw_arguments_text="{"),
            RawToolCall(provider_call_id="bad-2", raw_tool_name="lookup", raw_arguments_text="[]"),
        ),
        run_id=ctx.run_id, task_id=ctx.task_id, trial_index=ctx.trial_index, trial_seed=ctx.trial_seed,
        turn_index=ctx.turn_index, trusted_route=TrustedRouteContext(route_id="orchestrated-llm-agent", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    ).failures
    synthetic_first = sidecar.register(malformed[0])
    synthetic_second = sidecar.register(malformed[1])
    monkeypatch.setattr(environment, "get_response", lambda _call: (_ for _ in ()).throw(AssertionError("must not fall through")))
    orchestrator.message = AssistantMessage(role="assistant", tool_calls=[
        ToolCall(id="read", name="lookup", arguments={"key": "a"}), synthetic_first,
        ToolCall(id="write", name="increment", arguments={"amount": 2}), synthetic_second,
    ])
    orchestrator.from_role, orchestrator.to_role = Role.AGENT, Role.ENV
    orchestrator._harness_pending_agent_context = ctx
    orchestrator.step()
    messages = orchestrator.message.tool_messages
    assert [message.id for message in messages] == ["read", synthetic_first.id, "write", synthetic_second.id]
    assert [message.error for message in messages] == [False, True, False, True]
    assert environment.tools.db["writes"] == 2


def test_harness_disabled_step_matches_native_environment_and_teardown_unbinds_agent(tmp_path):
    native_environment = Environment(domain_name="test", policy="policy", tools=RuntimeToolkit())
    native = native_environment.get_response(ToolCall(id="same", name="lookup", arguments={"key": "x"}))
    harness, environment = make_harness(tmp_path)
    enabled = HarnessRuntimeBridge(harness).execute_assistant_call(
        ToolCall(id="same", name="lookup", arguments={"key": "x"}), context=context(harness)
    )
    assert native == enabled

    disabled_environment = Environment(domain_name="test", policy="policy", tools=RuntimeToolkit())
    disabled = Orchestrator("test", SimpleNamespace(), SimpleNamespace(), disabled_environment, SimpleNamespace(id="71"), seed=300)
    disabled.message = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="native", name="lookup", arguments={"key": "z"})])
    disabled.from_role, disabled.to_role = Role.AGENT, Role.ENV
    disabled.step()
    assert disabled.message.id == "native" and disabled.message.error is False
    assert disabled_environment.tools.db["calls"] == [{"tool": "lookup", "key": "z"}]

    agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    orchestrator = Orchestrator("test", agent, SimpleNamespace(), environment, SimpleNamespace(id="71"), seed=300, harness=harness)
    assert agent.harness_parser_enabled is True
    orchestrator._teardown_harness()
    assert agent.harness_parser_enabled is False

"""HAR-07D trace/lifecycle runtime propagation tests; no gateway."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import ToolCall
from tau2.data_model.message import AssistantMessage, UserMessage
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.episode import EpisodeHarnessFactory
from tau2.harness.entrypoint import A1RunSettings, finalize_episode_after_results_commit
from tau2.harness.contracts import Requestor, TrustedRouteContext
from tau2.harness.integration_contracts import LifecycleStatus
from tau2.harness.parser import ParserNormalizer, RawToolCall
from tau2.harness.runtime import HarnessRuntimeBridge
from tau2.harness.trace import TraceLoggerError
from tau2.orchestrator.orchestrator import Orchestrator
from tests.test_har07_runtime_bridge import context, make_harness


def phases(harness):
    return [json.loads(line)["phase"] for line in harness.lifecycle_store.trace_path.read_text().splitlines()]


def test_allow_and_reject_trace_phase_ordering_and_no_raw_payload(tmp_path):
    harness, _environment = make_harness(tmp_path)
    bridge = HarnessRuntimeBridge(harness)
    ctx = context(harness)
    bridge.execute_assistant_call(ToolCall(id="allow", name="lookup", arguments={"key": "x"}), context=ctx)
    assert phases(harness) == ["proposed", "parsed", "validated", "dispatch_started", "dispatch_completed", "result_emitted"]
    assert all('"raw_payload_json":null' in line for line in harness.lifecycle_store.trace_path.read_text().splitlines())

    reject_harness, _ = make_harness(tmp_path / "reject")
    HarnessRuntimeBridge(reject_harness).execute_assistant_call(
        ToolCall(id="reject", name="not_registered", arguments={}), context=context(reject_harness)
    )
    assert phases(reject_harness) == ["proposed", "parsed", "validated", "result_emitted"]


def test_parser_reject_trace_skips_validation_and_dispatch(tmp_path):
    harness, _environment = make_harness(tmp_path)
    ctx = context(harness)
    failure = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="bad", raw_tool_name="lookup", raw_arguments_text="{"),),
        run_id=ctx.run_id, task_id=ctx.task_id, trial_index=ctx.trial_index, trial_seed=ctx.trial_seed,
        turn_index=ctx.turn_index, trusted_route=TrustedRouteContext(route_id="agent", requestor=Requestor.ASSISTANT), parsed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    ).failures[0]
    envelope = harness.parser_failure_sidecar(turn_index=ctx.turn_index, requestor=ctx.requestor).register(failure)
    HarnessRuntimeBridge(harness).execute_assistant_call(envelope, context=ctx)
    assert phases(harness) == ["proposed", "parsed", "result_emitted"]


def test_logger_failure_before_dispatch_blocks_invocation_and_marks_lifecycle(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    bridge = HarnessRuntimeBridge(harness)
    monkeypatch.setattr(harness.trace_logger, "dispatch_started", lambda *_a, **_k: (_ for _ in ()).throw(TraceLoggerError("disk pre")))
    with pytest.raises(TraceLoggerError, match="disk pre"):
        bridge.execute_assistant_call(ToolCall(id="write", name="increment", arguments={"amount": 2}), context=context(harness))
    assert environment.tools.db["writes"] == 0
    assert harness.lifecycle_store.read().status == LifecycleStatus.FAILED_BEFORE_DISPATCH


def test_preflight_failure_is_supervisor_visible_and_cleans_constructor_resources(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    monkeypatch.setattr(harness.trace_logger, "preflight", lambda: (_ for _ in ()).throw(TraceLoggerError("preflight disk")))
    with pytest.raises(TraceLoggerError, match="preflight disk"):
        Orchestrator("test", LLMAgent(tools=[], domain_policy="p", llm="fake"), SimpleNamespace(), environment, SimpleNamespace(id="71"), seed=300, harness=harness)
    assert harness.lifecycle_store.read().status == LifecycleStatus.FAILED_BEFORE_DISPATCH


def test_logger_failure_after_write_preserves_side_effect_and_aborts(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    bridge = HarnessRuntimeBridge(harness)
    monkeypatch.setattr(harness.trace_logger, "dispatch_completed", lambda *_a, **_k: (_ for _ in ()).throw(TraceLoggerError("disk post")))
    with pytest.raises(TraceLoggerError, match="disk post"):
        bridge.execute_assistant_call(ToolCall(id="write", name="increment", arguments={"amount": 2}), context=context(harness))
    assert environment.tools.db["writes"] == 2
    lifecycle = harness.lifecycle_store.read()
    assert lifecycle.status == LifecycleStatus.FAILED_AFTER_DISPATCH
    assert lifecycle.side_effect_may_have_occurred is True


def test_logger_failure_after_read_still_marks_post_dispatch_and_propagates(tmp_path, monkeypatch):
    harness, environment = make_harness(tmp_path)
    monkeypatch.setattr(harness.trace_logger, "dispatch_completed", lambda *_a, **_k: (_ for _ in ()).throw(TraceLoggerError("read post")))
    with pytest.raises(TraceLoggerError, match="read post"):
        HarnessRuntimeBridge(harness).execute_assistant_call(
            ToolCall(id="read", name="lookup", arguments={"key": "x"}), context=context(harness)
        )
    assert environment.tools.db["calls"] == [{"tool": "lookup", "key": "x"}]
    assert harness.lifecycle_store.read().status == LifecycleStatus.FAILED_AFTER_DISPATCH


def test_lifecycle_closes_only_after_results_commit_and_orchestrator_failure_cleanup(tmp_path):
    harness, environment = make_harness(tmp_path)
    bridge = HarnessRuntimeBridge(harness)
    bridge.execute_assistant_call(ToolCall(id="read", name="lookup", arguments={"key": "x"}), context=context(harness))
    agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    _orchestrator = Orchestrator("test", agent, SimpleNamespace(), environment, SimpleNamespace(id="71"), seed=300, harness=harness)
    # Orchestrator completion alone must not claim that evaluator/serialization
    # completed successfully.
    assert harness.lifecycle_store.read().status == LifecycleStatus.OPEN
    settings = A1RunSettings.create(
        trace_root=tmp_path, domain="test", task_ids=("71",), agent="llm_agent",
        user="user_simulator", llm_agent="agent", llm_user="user", num_trials=1,
        trial_seeds=(300,), max_steps=20, max_errors=2, max_concurrency=1,
        root_seed=300, llm_args_agent={}, llm_args_user={},
    )
    settings.write_or_validate_config()
    finalize_episode_after_results_commit(settings, harness.identity)
    lifecycle = harness.lifecycle_store.read()
    raw = harness.lifecycle_store.trace_path.read_bytes()
    assert lifecycle.status == LifecycleStatus.CLOSED
    assert lifecycle.trace_sha256 == hashlib.sha256(raw).hexdigest()
    assert lifecycle.trace_event_count == len(raw.splitlines()) == 6
    assert lifecycle.trace_last_sequence == 5

    failed_harness, failed_environment = make_harness(tmp_path / "failed")
    failed_agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    failed = Orchestrator("test", failed_agent, SimpleNamespace(), failed_environment, SimpleNamespace(id="71"), seed=300, harness=failed_harness)
    failed.initialize = lambda: (_ for _ in ()).throw(RuntimeError("orchestrator boom"))
    with pytest.raises(RuntimeError, match="orchestrator boom"):
        failed.run()
    assert failed_agent.harness_parser_enabled is False
    assert failed_harness.lifecycle_store.read().status == LifecycleStatus.FAILED_OPERATIONAL

    cleanup_harness, cleanup_environment = make_harness(tmp_path / "cleanup")
    cleanup_agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    cleanup = Orchestrator("test", cleanup_agent, SimpleNamespace(), cleanup_environment, SimpleNamespace(id="71"), seed=300, harness=cleanup_harness)
    cleanup.initialize = lambda: (_ for _ in ()).throw(RuntimeError("primary failure"))
    cleanup_harness.close_resources = lambda: (_ for _ in ()).throw(RuntimeError("cleanup failure"))
    with pytest.raises(RuntimeError, match="primary failure"):
        cleanup.run()


def test_failed_operational_lifecycle_snapshots_existing_trace(tmp_path):
    harness, _environment = make_harness(tmp_path)
    HarnessRuntimeBridge(harness).execute_assistant_call(
        ToolCall(id="read", name="lookup", arguments={"key": "x"}), context=context(harness)
    )
    lifecycle = harness.lifecycle_store.read()
    harness.lifecycle_store.terminalize(
        lifecycle, status=LifecycleStatus.FAILED_OPERATIONAL,
        outcome="failed", error="provider exhausted", side_effect=True,
    )
    raw = harness.lifecycle_store.trace_path.read_bytes()
    terminal = harness.lifecycle_store.read()
    assert terminal.trace_sha256 == hashlib.sha256(raw).hexdigest()
    assert terminal.trace_event_count == len(raw.splitlines())
    assert terminal.trace_last_sequence == len(raw.splitlines()) - 1
    assert terminal.side_effect_may_have_occurred is True


class UserToolkit(ToolKitBase):
    @is_tool(ToolType.READ)
    def user_lookup(self, key: str) -> str:
        """Native-only user lookup for scope testing."""
        return key


class CountingEnvironment(Environment):
    def __init__(self, *args, **kwargs):
        self.make_count = 0
        self.sync_count = 0
        super().__init__(*args, **kwargs)
        self.make_count = 0
        self.sync_count = 0

    def make_tool_call(self, *args, **kwargs):
        self.make_count += 1
        return super().make_tool_call(*args, **kwargs)

    def sync_tools(self):
        self.sync_count += 1


def test_wrapper_matches_native_make_sync_counts_and_user_calls_remain_native(tmp_path, monkeypatch):
    from tests.test_har07_runtime_bridge import RuntimeToolkit, identity

    native_env = CountingEnvironment(domain_name="test", policy="p", tools=RuntimeToolkit(), user_tools=UserToolkit())
    native = Orchestrator("test", SimpleNamespace(), SimpleNamespace(), native_env, SimpleNamespace(id="71"), seed=300)
    native.message = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="a", name="lookup", arguments={"key": "x"})])
    from tau2.orchestrator.orchestrator import Role
    native.from_role, native.to_role = Role.AGENT, Role.ENV
    native.step()
    assert native_env.make_count == 1 and native_env.sync_count == 2

    harness_envs = []
    def make_environment():
        env = CountingEnvironment(domain_name="test", policy="p", tools=RuntimeToolkit(), user_tools=UserToolkit())
        harness_envs.append(env)
        return env
    harness = EpisodeHarnessFactory(trace_root=tmp_path, environment_factory=make_environment).create(identity())
    agent = LLMAgent(tools=[], domain_policy="p", llm="fake")
    enabled = Orchestrator("test", agent, SimpleNamespace(), harness.environment, SimpleNamespace(id="71"), seed=300, harness=harness)
    enabled.message = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="a", name="lookup", arguments={"key": "x"})])
    enabled.from_role, enabled.to_role = Role.AGENT, Role.ENV
    enabled._harness_pending_agent_context = context(harness, turn=0)
    enabled.step()
    assert harness.environment.make_count == 1 and harness.environment.sync_count == 2
    assert harness.environment.tools.db["calls"] == [{"tool": "lookup", "key": "x"}]
    entry = next(item for item in harness.registry_snapshot.entries if item.tool_name == "lookup")
    model = harness.params_model_for(requestor=Requestor.ASSISTANT, tool_name="lookup")
    assert entry.params_model_identity == f"{model.__module__}:{model.__qualname__}"

    monkeypatch.setattr(enabled._harness_runtime, "execute_assistant_call", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("user call entered assistant harness")))
    enabled.message = UserMessage(role="user", tool_calls=[ToolCall(id="u", name="user_lookup", arguments={"key": "native"}, requestor="user")])
    enabled.from_role, enabled.to_role = Role.USER, Role.ENV
    enabled.step()
    assert enabled.message.error is False and enabled.message.requestor == "user"

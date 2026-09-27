"""HAR-07E local full-episode clean-path parity; no model provider/gateway."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.environment.environment import Environment
from tau2.harness.episode import EpisodeHarnessFactory
from tau2.harness.entrypoint import A1RunSettings, finalize_episode_after_results_commit
from tau2.harness.integration_contracts import EpisodeIdentity, LifecycleStatus
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.user.base import BaseUser
from tests.test_har07_runtime_bridge import RuntimeToolkit


class ScriptedUser(BaseUser):
    def get_init_state(self, message_history=None):
        return None

    def generate_next_message(self, message, state):
        return UserMessage(role="user", content="please continue"), state

    def set_seed(self, seed):
        return None

    @classmethod
    def is_stop(cls, message):
        return False


class ScriptedLLMAgent(LLMAgent):
    def __init__(self, calls):
        super().__init__(tools=[], domain_policy="policy", llm="scripted")
        self._calls = calls
        self._turn = 0

    def generate_next_message(self, message, state):
        if self._turn == 0:
            self._turn += 1
            # Mirror the real LLMAgent path: provider generation requests the
            # episode-turn context before returning an assistant tool call.
            if self.harness_parser_enabled:
                self._harness_parser_context_supplier()
            return AssistantMessage(role="assistant", tool_calls=self._calls), state
        return AssistantMessage(role="assistant", content="STOP"), state

    @classmethod
    def is_stop(cls, message):
        return message.content == "STOP"


def task():
    return SimpleNamespace(id="71", initial_state=None)


def identity():
    return EpisodeIdentity(run_id="har07e", task_id="71", trial_index=0, trial_seed=300, episode_attempt=1)


def run_native(calls):
    environment = Environment(domain_name="test", policy="policy", tools=RuntimeToolkit())
    simulation = Orchestrator(
        "test", ScriptedLLMAgent(calls), ScriptedUser(), environment, task(),
        max_steps=10, max_errors=10, seed=300,
    ).run()
    return simulation, environment


def run_harness(calls, trace_root):
    environments = []
    def make_environment():
        environment = Environment(domain_name="test", policy="policy", tools=RuntimeToolkit())
        environments.append(environment)
        return environment
    harness = EpisodeHarnessFactory(trace_root=trace_root, environment_factory=make_environment).create(identity())
    simulation = Orchestrator(
        "test", ScriptedLLMAgent(calls), ScriptedUser(), harness.environment, task(),
        max_steps=10, max_errors=10, seed=300, harness=harness,
    ).run()
    return simulation, harness, environments[0]


def finalize_after_results_commit(trace_root, harness):
    settings = A1RunSettings.create(
        trace_root=trace_root, domain="test", task_ids=("71",), agent="llm_agent",
        user="user_simulator", llm_agent="scripted", llm_user="scripted",
        num_trials=1, trial_seeds=(300,), max_steps=10, max_errors=10,
        max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={},
    )
    settings.write_or_validate_config()
    finalize_episode_after_results_commit(settings, harness.identity)


def visible(messages):
    rows = []
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        tool_calls = None if calls is None else [
            (call.id, call.name, call.arguments, call.requestor) for call in message.tool_calls
        ]
        rows.append((message.role, message.content, tool_calls, getattr(message, "requestor", None), getattr(message, "error", None)))
    return rows


@pytest.mark.parametrize("calls", [
    [ToolCall(id="read", name="lookup", arguments={"key": "x"})],
    [ToolCall(id="write", name="increment", arguments={"amount": 2})],
    [
        ToolCall(id="read", name="lookup", arguments={"key": "x"}),
        ToolCall(id="write", name="increment", arguments={"amount": 2}),
    ],
], ids=["valid_read", "valid_write", "clean_multi_call"])
def test_native_and_harness_clean_episode_parity(calls, tmp_path):
    native, native_environment = run_native(calls)
    enabled, harness, harness_environment = run_harness(calls, tmp_path)

    assert native.termination_reason == enabled.termination_reason == "agent_stop"
    assert visible(native.messages) == visible(enabled.messages)
    assert native_environment.tools.db == harness_environment.tools.db
    assert [row["tool"] for row in native_environment.tools.db["calls"]] == [row["tool"] for row in harness_environment.tools.db["calls"]]
    assert all(
        not call.id.startswith("hparse_")
        for message in enabled.messages
        for call in (getattr(message, "tool_calls", None) or [])
    )

    # A completed execution remains open until the caller has committed its
    # native result row; direct orchestrator tests model that commit here.
    assert harness.lifecycle_store.read().status == LifecycleStatus.OPEN
    finalize_after_results_commit(tmp_path, harness)
    lifecycle = harness.lifecycle_store.read()
    raw = harness.lifecycle_store.trace_path.read_bytes()
    events = [json.loads(line) for line in raw.splitlines()]
    assert lifecycle.status == LifecycleStatus.CLOSED
    assert lifecycle.trace_sha256 == hashlib.sha256(raw).hexdigest()
    assert lifecycle.trace_event_count == len(events) == 6 * len(calls)
    assert lifecycle.trace_last_sequence == len(events) - 1
    assert [event["sequence"] for event in events] == list(range(len(events)))
    for offset in range(0, len(events), 6):
        assert [event["phase"] for event in events[offset:offset + 6]] == [
            "proposed", "parsed", "validated", "dispatch_started", "dispatch_completed", "result_emitted"
        ]

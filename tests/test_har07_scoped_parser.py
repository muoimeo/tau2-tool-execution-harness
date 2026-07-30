"""HAR-07B scoped parser integration tests; no native dispatch or gateway."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tau2.agent.llm_agent import LLMAgent, LLMGTAgent, LLMSoloAgent
from tau2.data_model.message import SystemMessage, UserMessage
from tau2.environment.utils.interface_agent import InterfaceAgent
from tau2.evaluator import evaluator_nl_assertions
from tau2.harness.contracts import Requestor
from tau2.harness.integration_contracts import (
    OrchestratedHarnessContext,
    ParserFailureSidecar,
)
from tau2.harness.scoped_parser import parse_orchestrated_provider_tool_calls
from tau2.user.user_simulator import UserSimulator
from tau2.utils import llm_utils

NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def context(turn: int = 5) -> OrchestratedHarnessContext:
    return OrchestratedHarnessContext(
        run_id="a1-run", task_id="71", trial_index=2, trial_seed=300,
        turn_index=turn, requestor=Requestor.ASSISTANT,
    )


def provider_call(call_id: str | None, name: str | None, arguments: str):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeChoice:
    def __init__(self, calls):
        self.finish_reason = "tool_calls"
        self.message = SimpleNamespace(role="assistant", content=None, tool_calls=calls)

    def to_dict(self):
        return {"fake": True}


class FakeResponse:
    def __init__(self, calls):
        self.model = "fake"
        self.choices = [FakeChoice(calls)]

    def get(self, _key):
        return None


def patch_completion(monkeypatch, calls):
    monkeypatch.setattr(llm_utils, "completion", lambda **_kwargs: FakeResponse(calls))
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _response: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _response: None)


def test_disabled_generate_keeps_native_path_and_never_invokes_scoped_parser(monkeypatch):
    calls = [provider_call("provider-1", "lookup", '{"order_id":"#W1"}')]
    patch_completion(monkeypatch, calls)
    invoked = False

    def unexpected(**_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("scoped parser must not run without context")

    monkeypatch.setattr(llm_utils, "parse_orchestrated_provider_tool_calls", unexpected)
    result = llm_utils.generate(
        "fake", [SystemMessage(role="system", content="s"), UserMessage(role="user", content="u")]
    )
    assert invoked is False
    assert result.tool_calls[0].id == "provider-1"
    assert result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].arguments == {"order_id": "#W1"}


def test_harness_generate_routes_raw_arguments_through_har03_before_native_materialization(monkeypatch):
    calls = [provider_call("provider-1", "lookup", '{"order_id":"#W1"}')]
    patch_completion(monkeypatch, calls)
    ctx = context()
    sidecar = ParserFailureSidecar(ctx)
    original = llm_utils.parse_orchestrated_provider_tool_calls
    seen = []

    def wrapped(*args, **kwargs):
        seen.append(args[0][0].function.arguments)
        return original(*args, **kwargs)

    monkeypatch.setattr(llm_utils, "parse_orchestrated_provider_tool_calls", wrapped)
    result = llm_utils.generate(
        "fake", [SystemMessage(role="system", content="s"), UserMessage(role="user", content="u")],
        harness_context=ctx, parser_failure_sidecar=sidecar,
    )
    assert seen == ['{"order_id":"#W1"}']
    assert result.tool_calls[0].id == "provider-1"
    assert result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].arguments == {"order_id": "#W1"}


def test_multicall_order_malformed_ordinals_and_consume_once_fail_closed():
    ctx = context()
    sidecar = ParserFailureSidecar(ctx)
    raw = [
        provider_call("valid-1", "first", '{"x":1}'),
        provider_call("bad-1", "broken_one", "{MALFORMED_SECRET"),
        provider_call("valid-2", "second", '{"y":2}'),
        provider_call("bad-2", "broken_two", "[]"),
    ]
    native_calls, batch = parse_orchestrated_provider_tool_calls(
        raw, context=ctx, sidecar=sidecar, parsed_at=NOW,
    )
    assert [call.name for call in native_calls] == ["first", "broken_one", "second", "broken_two"]
    assert [call.id for call in native_calls if call.id.startswith("hparse_")] == [
        batch.failures[0].synthetic_call_id, batch.failures[1].synthetic_call_id
    ]
    assert [failure.raw_call_index for failure in batch.failures] == [1, 3]
    assert [failure.malformed_call_ordinal for failure in batch.failures] == [0, 1]
    assert native_calls[1].arguments == native_calls[3].arguments == {}
    assert "raw_arguments_text" not in native_calls[1].model_dump_json()
    assert "MALFORMED_SECRET" not in native_calls[1].model_dump_json()
    with pytest.raises(ValueError, match="duplicate parser failure synthetic ID"):
        sidecar.register(batch.failures[0])

    failure = sidecar.consume(native_calls[1].id, ctx)
    assert failure.error_code.value == "PARSE_MALFORMED_JSON"
    with pytest.raises(KeyError, match="unknown or already-consumed"):
        sidecar.consume(native_calls[1].id, ctx)
    with pytest.raises(KeyError, match="unknown or already-consumed"):
        sidecar.consume("hparse_unknown", ctx)
    with pytest.raises(ValueError, match="context mismatch"):
        sidecar.consume(
            native_calls[3].id,
            OrchestratedHarnessContext(
                run_id="a1-run", task_id="71", trial_index=2, trial_seed=300,
                turn_index=6, requestor=Requestor.ASSISTANT,
            ),
        )


def test_non_string_provider_arguments_are_fail_closed_as_parser_structure_rejection():
    ctx = context()
    sidecar = ParserFailureSidecar(ctx)
    native_calls, batch = parse_orchestrated_provider_tool_calls(
        [SimpleNamespace(id="bad", function=SimpleNamespace(name="lookup", arguments={"not": "text"}))],
        context=ctx, sidecar=sidecar, parsed_at=NOW,
    )
    assert batch.failures[0].error_code.value == "PARSE_RESPONSE_STRUCTURE_INVALID"
    assert native_calls[0].arguments == {}


def test_only_llmagent_exposes_the_explicit_harness_opt_in(monkeypatch):
    captured = []

    def fake_generate(**kwargs):
        captured.append(kwargs)
        from tau2.data_model.message import AssistantMessage
        return AssistantMessage(role="assistant", content="ok")

    monkeypatch.setattr("tau2.agent.llm_agent.generate", fake_generate)
    agent = LLMAgent(tools=[], domain_policy="policy", llm="fake")
    state = agent.get_init_state()
    agent.generate_next_message(UserMessage(role="user", content="hello"), state)
    assert "harness_context" not in captured[-1]

    ctx = context()
    sidecar = ParserFailureSidecar(ctx)
    agent.enable_harness_parser(context_supplier=lambda: ctx, sidecar_supplier=lambda value: sidecar)
    agent.generate_next_message(UserMessage(role="user", content="again"), state)
    assert captured[-1]["harness_context"] == ctx
    assert captured[-1]["parser_failure_sidecar"] is sidecar


def test_evaluator_user_simulator_and_interface_agent_source_do_not_pass_harness_context():
    # These are the native non-agent callers identified in the HAR-07B audit.
    for callable_ in (
        UserSimulator.generate_next_message,
        evaluator_nl_assertions.NLAssertionsEvaluator.evaluate_nl_assertions,
        InterfaceAgent.respond,
        LLMGTAgent.generate_next_message,
        LLMSoloAgent.generate_next_message,
    ):
        assert "harness_context" not in inspect.getsource(callable_)

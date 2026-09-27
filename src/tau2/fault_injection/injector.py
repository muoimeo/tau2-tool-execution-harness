"""Provider-boundary FI-v1 fault injection; no prompt mutation and no dispatch."""

from __future__ import annotations

from copy import deepcopy

from tau2.fault_injection.contracts import (
    CompiledFixture,
    FaultKind,
    FaultScenario,
    ProviderAction,
    ProviderActionKind,
)


class InjectionError(ValueError):
    pass


def _tool_action(name: str, arguments: str) -> ProviderAction:
    return ProviderAction.response_action(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "fi-call-0001", "type": "function", "function": {"name": name, "arguments": arguments}}
            ],
        },
        finish_reason="tool_calls",
    )


def _reasoning_only() -> ProviderAction:
    return ProviderAction.response_action(
        {"role": "assistant", "content": None, "tool_calls": [], "reasoning_content": "internal-only"},
        finish_reason="stop",
    )


def _a3_repair_action(arguments: str) -> ProviderAction:
    return _tool_action(
        "find_user_id_by_name_zip",
        arguments,
    )


class A3NativeResultFaultInjector:
    """FI-only raw native-result boundary injector.

    Provider responses cannot represent a native callable's return value or a
    post-call DB discrepancy. This wrapper calls the real native function once
    and changes only the value/state observed by A3 immediately afterwards.
    It is never installed by normal ``tau2 run`` paths.
    """

    def __init__(self, fault: FaultKind, *, expected_tool: str):
        self.fault = fault
        self.expected_tool = expected_tool
        self.invocations = 0
        self._used = False

    def bind(self, environment) -> None:
        original = environment.make_tool_call

        def injected(tool_name: str, requestor: str = "assistant", **kwargs):
            result = original(tool_name, requestor=requestor, **kwargs)
            if self._used or requestor != "assistant" or tool_name != self.expected_tool:
                return result
            self._used = True
            self.invocations += 1
            if self.fault == FaultKind.A3_INVALID_OUTPUT_AFTER_WRITE:
                return None
            if self.fault == FaultKind.A3_POSTCONDITION_FAIL_AFTER_WRITE:
                order = environment.tools.db.orders[kwargs["order_id"]]
                # A deterministic contradiction to the frozen cancel rule,
                # injected only after the genuine callable completed.
                order.cancel_reason = "__fi_postcondition_mismatch__"
                return result
            return result

        environment.make_tool_call = injected


def apply_fault(fixture: CompiledFixture, scenario: FaultScenario) -> CompiledFixture:
    """Return a new scripted fixture with an exact provider response fault.

    P/D faults are OpenAI tool-call payloads, so HAR-03/registry/schema/domain
    run normally.  R/S faults are transport or response-shape outcomes.
    """
    if scenario.fixture_id != fixture.fixture_id:
        raise InjectionError("scenario fixture_id does not match fixture")
    queue = list(fixture.agent_actions if scenario.target_role == "agent" else fixture.user_actions)
    if scenario.target_ordinal < 0 or scenario.target_ordinal >= len(queue):
        raise InjectionError("scenario target ordinal is outside fixture provider queue")
    original = queue[scenario.target_ordinal]
    fault = scenario.fault
    if fault == FaultKind.CLEAN_REPLAY:
        replacement = original
    elif fault == FaultKind.MALFORMED_JSON:
        replacement = _tool_action("find_user_id_by_name_zip", '{"first_name":"Noah"')
    elif fault == FaultKind.JSON_NON_OBJECT:
        replacement = _tool_action("find_user_id_by_name_zip", '["not","an","object"]')
    elif fault == FaultKind.UNKNOWN_TOOL:
        replacement = _tool_action("fi_unknown_tool", "{}")
    elif fault == FaultKind.MISSING_REQUIRED_ARGUMENT:
        replacement = _tool_action("find_user_id_by_name_zip", '{"first_name":"Noah","last_name":"Ito"}')
    elif fault == FaultKind.EXTRA_ARGUMENT:
        replacement = _tool_action("find_user_id_by_name_zip", '{"first_name":"Noah","last_name":"Ito","zip":"98187","__fi_extra__":true}')
    elif fault == FaultKind.WRONG_STRICT_TYPE:
        replacement = _tool_action("modify_pending_order_items", '{"order_id":"#W0000000","item_ids":"not-a-list","new_item_ids":["2"],"payment_method_id":"credit_card_1"}')
    elif fault == FaultKind.DOMAIN_CONSTRAINT:
        replacement = _tool_action("modify_pending_order_items", '{"order_id":"#W0000000","item_ids":["1"],"new_item_ids":["1"],"payment_method_id":"credit_card_1"}')
    elif fault == FaultKind.NATIVE_PRECONDITION:
        replacement = _tool_action("find_user_id_by_name_zip", '{"first_name":"Nobody","last_name":"Missing","zip":"00000"}')
    elif fault == FaultKind.TIMEOUT_ONCE:
        # FI's local client timeout is two seconds (see runner.py).  The old
        # one-second delay returned without a response *before* that deadline,
        # which LiteLLM surfaced as a connection-close APIError rather than a
        # typed timeout.  Deliberately exceed the client deadline so the
        # production LiteLLM adapter emits its timeout exception path.
        replacement = ProviderAction(kind=ProviderActionKind.TIMEOUT, sleep_seconds=3.0)
    elif fault == FaultKind.HTTP_503_ONCE:
        replacement = ProviderAction(kind=ProviderActionKind.HTTP_ERROR, http_status=503, error_type="service_unavailable")
    elif fault == FaultKind.REASONING_ONLY_ONCE:
        replacement = _reasoning_only()
    elif fault in {FaultKind.PROVIDER_EXHAUST_PRE_WRITE, FaultKind.PROVIDER_EXHAUST_POST_WRITE}:
        # A2 owns exactly three total provider attempts.  Insert three server
        # failures at one generation boundary; the original scripted response
        # is intentionally not reached for that generation.
        failures = [
            ProviderAction(kind=ProviderActionKind.HTTP_ERROR, http_status=503, error_type="service_unavailable")
            for _ in range(3)
        ]
        queue[scenario.target_ordinal:scenario.target_ordinal + 1] = failures
        values = fixture.model_dump()
        if scenario.target_role == "agent":
            values["agent_actions"] = queue
        else:
            values["user_actions"] = queue
        return CompiledFixture.model_validate(deepcopy(values))
    elif fault == FaultKind.A3_FENCED_JSON_REPAIR:
        replacement = _a3_repair_action('```json\n{"first_name":"Noah","last_name":"Ito","zip":"98187"}\n```')
    elif fault == FaultKind.A3_DOUBLE_ENCODED_JSON_REPAIR:
        replacement = _a3_repair_action('"{\\"first_name\\":\\"Noah\\",\\"last_name\\":\\"Ito\\",\\"zip\\":\\"98187\\"}"')
    elif fault == FaultKind.A3_TRAILING_COMMA_REPAIR:
        replacement = _a3_repair_action('{"first_name":"Noah","last_name":"Ito","zip":"98187",}')
    elif fault == FaultKind.A3_TRUNCATED_JSON_NO_REPAIR:
        replacement = _a3_repair_action('{"first_name":"Noah"')
    elif fault == FaultKind.A3_REPAIRED_SCHEMA_REJECT:
        replacement = _a3_repair_action('```json\n{"first_name":"Noah"}\n```')
    elif fault in {FaultKind.A3_INVALID_OUTPUT_AFTER_WRITE, FaultKind.A3_POSTCONDITION_FAIL_AFTER_WRITE}:
        replacement = original
    else:  # defensive future-proofing
        raise InjectionError(f"unsupported FI-v1 fault: {fault}")
    queue[scenario.target_ordinal] = replacement
    values = fixture.model_dump()
    if scenario.target_role == "agent":
        values["agent_actions"] = queue
    else:
        values["user_actions"] = queue
    # The source fixture hash remains source provenance; scenario identity is
    # held by FaultScenario, avoiding accidental rewriting of frozen fixtures.
    return CompiledFixture.model_validate(deepcopy(values))

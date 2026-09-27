from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from tau2.harness.a1.contracts import Requestor, TrustedRouteContext
from tau2.harness.a1.parser import ParserNormalizer, RawToolCall
from tau2.harness.a1.scoped_parser import parse_orchestrated_provider_tool_calls
from tau2.harness.a1.integration_contracts import OrchestratedHarnessContext, ParserFailureSidecar
from tau2.harness.a3.audit import RecoveryAuditTrail
from tau2.harness.a3.output_validation import validate_output
from tau2.harness.a3.postconditions import capture_after, capture_before, evaluate
from tau2.harness.a3.repair import A3Repairer
from tau2.harness.a3.runtime import A3PostDispatchReviewer, A3QuarantineRequired
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.a1.episode import EpisodeHarnessFactory
from tau2.harness.a1.integration_contracts import EpisodeIdentity
from tau2.harness.a1.runtime import HarnessRuntimeBridge


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def context() -> OrchestratedHarnessContext:
    return OrchestratedHarnessContext(
        run_id="a3-test", task_id="1", trial_index=0, trial_seed=300,
        turn_index=2, requestor=Requestor.ASSISTANT,
    )


def parser_failure(raw: str):
    batch = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="call-1", raw_tool_name="cancel_pending_order", raw_arguments_text=raw),),
        run_id="a3-test", task_id="1", trial_index=0, trial_seed=300,
        turn_index=2, trusted_route=TrustedRouteContext(route_id="test", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    )
    assert len(batch.failures) == 1
    return batch.failures[0]


@pytest.mark.parametrize("raw", [
    '```json\n{"order_id":"#W1","reason":"ordered by mistake"}\n```',
    '"{\\"order_id\\":\\"#W1\\",\\"reason\\":\\"ordered by mistake\\"}"',
    '{"order_id":"#W1","reason":"ordered by mistake",}',
])
def test_a3_repairs_only_representation_and_candidate_is_parseable(raw):
    candidate = A3Repairer().try_repair(parser_failure(raw))
    assert candidate is not None
    reparsed = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="call-1", raw_tool_name="cancel_pending_order", raw_arguments_text=candidate.repaired_payload),),
        run_id="a3-test", task_id="1", trial_index=0, trial_seed=300,
        turn_index=2, trusted_route=TrustedRouteContext(route_id="test", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    )
    assert len(reparsed.requests) == 1
    assert reparsed.requests[0].tool_name == "cancel_pending_order"


def test_a3_never_repairs_truncated_json_or_missing_business_fields():
    assert A3Repairer().try_repair(parser_failure('{"order_id":"#W1"')) is None
    # This is already valid JSON, so A3 is never invoked; it cannot add reason.
    batch = ParserNormalizer().parse(
        (RawToolCall(provider_call_id="call-1", raw_tool_name="cancel_pending_order", raw_arguments_text='{"order_id":"#W1"}'),),
        run_id="a3-test", task_id="1", trial_index=0, trial_seed=300,
        turn_index=2, trusted_route=TrustedRouteContext(route_id="test", requestor=Requestor.ASSISTANT), parsed_at=NOW,
    )
    assert batch.requests[0].arguments_json == '{"order_id":"#W1"}'


def test_a3_scoped_repair_runs_after_a1_failure_and_a3_off_keeps_rejection(tmp_path):
    raw = [SimpleNamespace(id="call-1", function=SimpleNamespace(name="cancel_pending_order", arguments='```json\n{"order_id":"#W1","reason":"ordered by mistake"}\n```'))]
    sidecar = ParserFailureSidecar(context())
    native, _ = parse_orchestrated_provider_tool_calls(raw, context=context(), sidecar=sidecar, parsed_at=NOW)
    assert native[0].id.startswith("hparse_")
    repaired_sidecar = ParserFailureSidecar(context())
    audit = RecoveryAuditTrail(tmp_path / "recovery.jsonl")
    native, _ = parse_orchestrated_provider_tool_calls(
        raw, context=context(), sidecar=repaired_sidecar, parsed_at=NOW,
        repairer=A3Repairer(), recovery_audit=audit,
    )
    assert native[0].id == "call-1"
    assert native[0].arguments["reason"] == "ordered by mistake"
    assert not repaired_sidecar.contains("call-1", context())
    assert len(audit.path.read_bytes().splitlines()) == 1


class RepairToolkit(ToolKitBase):
    def __init__(self):
        super().__init__(db={"writes": 0})

    @is_tool(ToolType.WRITE)
    def cancel_pending_order(self, order_id: str, reason: str) -> dict:
        """Deterministic fixture WRITE for exercising A1 after A3 repair."""
        self.db["writes"] += 1
        return {"order_id": order_id, "reason": reason}


class A3Order(BaseModel):
    status: str
    cancel_reason: str | None = None


class A3DB(BaseModel):
    orders: dict[str, A3Order]
    write_calls: int = 0


class A3RetailLikeToolkit(ToolKitBase):
    def __init__(self, *, invalid_output: bool):
        super().__init__(db=A3DB(orders={"#W1": A3Order(status="pending")}))
        self.invalid_output = invalid_output

    @is_tool(ToolType.WRITE)
    def cancel_pending_order(self, order_id: str, reason: str) -> dict:
        self.db.write_calls += 1
        order = self.db.orders[order_id]
        order.status, order.cancel_reason = "cancelled", reason
        return None if self.invalid_output else order.model_dump()


def test_repaired_call_reenters_registry_schema_domain_and_dispatches_once(tmp_path):
    raw = [SimpleNamespace(id="call-1", function=SimpleNamespace(name="cancel_pending_order", arguments='```json\n{"order_id":"#W1","reason":"ordered by mistake"}\n```'))]
    environment = Environment(domain_name="a3-test", policy="policy", tools=RepairToolkit())
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path, environment_factory=lambda: environment,
    ).create(
        EpisodeIdentity(run_id="a3-test", task_id="1", trial_index=0, trial_seed=300, episode_attempt=1),
        a3_recovery=True,
    )
    ctx = harness.context_for_turn(turn_index=2, requestor=Requestor.ASSISTANT)
    sidecar = harness.parser_failure_sidecar(turn_index=2, requestor=Requestor.ASSISTANT)
    tool_calls, _ = parse_orchestrated_provider_tool_calls(
        raw, context=ctx, sidecar=sidecar, parsed_at=NOW,
        repairer=harness.a3_repairer, recovery_audit=harness.a3_recovery_audit,
        repair_lineage=harness.a3_repair_lineage,
    )
    result = HarnessRuntimeBridge(harness).execute_assistant_call(tool_calls[0], context=ctx)
    assert result.error is False
    assert environment.tools.db["writes"] == 1
    events = harness.a3_recovery_audit.path.read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    assert '"repair_attempt_count":1' in events[-1]
    assert '"disposition":"repaired_dispatched"' in events[-1]
    assert harness.a3_repair_lineage == {}


def test_unrepairable_call_stays_a1_reject_and_never_dispatches(tmp_path):
    raw = [SimpleNamespace(id="call-1", function=SimpleNamespace(name="cancel_pending_order", arguments='{"order_id":"#W1"'))]
    environment = Environment(domain_name="a3-test", policy="policy", tools=RepairToolkit())
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path, environment_factory=lambda: environment,
    ).create(EpisodeIdentity(run_id="a3-test", task_id="1", trial_index=0, trial_seed=300, episode_attempt=1))
    ctx = harness.context_for_turn(turn_index=2, requestor=Requestor.ASSISTANT)
    sidecar = harness.parser_failure_sidecar(turn_index=2, requestor=Requestor.ASSISTANT)
    tool_calls, _ = parse_orchestrated_provider_tool_calls(
        raw, context=ctx, sidecar=sidecar, parsed_at=NOW, repairer=A3Repairer(),
    )
    result = HarnessRuntimeBridge(harness).execute_assistant_call(tool_calls[0], context=ctx)
    assert result.error is True
    assert environment.tools.db["writes"] == 0


def test_repaired_payload_still_fails_a1_schema_and_never_dispatches(tmp_path):
    raw = [SimpleNamespace(id="call-1", function=SimpleNamespace(name="cancel_pending_order", arguments='```json\n{"order_id":"#W1"}\n```'))]
    environment = Environment(domain_name="a3-test", policy="policy", tools=RepairToolkit())
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path, environment_factory=lambda: environment,
    ).create(EpisodeIdentity(run_id="a3-test", task_id="1", trial_index=0, trial_seed=300, episode_attempt=1))
    ctx = harness.context_for_turn(turn_index=2, requestor=Requestor.ASSISTANT)
    calls, _ = parse_orchestrated_provider_tool_calls(
        raw, context=ctx,
        sidecar=harness.parser_failure_sidecar(turn_index=2, requestor=Requestor.ASSISTANT),
        parsed_at=NOW, repairer=A3Repairer(),
    )
    response = HarnessRuntimeBridge(harness).execute_assistant_call(calls[0], context=ctx)
    assert response.error is True
    assert environment.tools.db["writes"] == 0


class ReturnedOrder(BaseModel):
    status: str
    cancel_reason: str | None


class ReturnedOrderContract(BaseModel):
    returns: ReturnedOrder


def test_a3_output_contract_detects_none_wrong_type_and_missing_field():
    assert validate_output(None, returns_model=ReturnedOrderContract).valid is False
    assert validate_output("not-order", returns_model=ReturnedOrderContract).valid is False
    assert validate_output({"status": "cancelled"}, returns_model=ReturnedOrderContract).valid is False
    assert validate_output({"status": "cancelled", "cancel_reason": "ordered by mistake"}, returns_model=ReturnedOrderContract).valid is True


def retail_env(status="pending", reason=None):
    order = SimpleNamespace(status=status, cancel_reason=reason)
    return SimpleNamespace(tools=SimpleNamespace(db=SimpleNamespace(orders={"#W1": order}))), order


def test_a3_postcondition_checks_explicit_retail_cancel_fields_only():
    environment, order = retail_env()
    plan = capture_before(environment, tool_name="cancel_pending_order", arguments={"order_id":"#W1", "reason":"ordered by mistake"})
    order.status, order.cancel_reason = "cancelled", "ordered by mistake"
    observed, digest = capture_after(environment, plan)
    assert evaluate(plan, observed, digest).passed is True
    order.cancel_reason = "no longer needed"
    observed, digest = capture_after(environment, plan)
    assert evaluate(plan, observed, digest).passed is False


def test_a3_post_dispatch_quarantines_invalid_write_without_replay(tmp_path):
    environment, order = retail_env()
    reviewer = A3PostDispatchReviewer(environment, RecoveryAuditTrail(tmp_path / "recovery.jsonl"))
    request = SimpleNamespace(
        arguments_json='{"order_id":"#W1","reason":"ordered by mistake"}',
        tool_name="cancel_pending_order", logical_call_id="call-1", request_sha256="a" * 64,
    )
    before = reviewer.before_dispatch(request, native_tool_type="write")
    # Simulate exactly one native mutation followed by an unusable return.
    order.status, order.cancel_reason = "cancelled", "ordered by mistake"
    with pytest.raises(A3QuarantineRequired) as error:
        reviewer.after_native_return(request, raw_result=None, returns_model=ReturnedOrderContract, review=before)
    assert error.value.side_effect_may_have_occurred is True
    rows = (tmp_path / "recovery.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert "quarantined_output_invalid" in rows[0]


@pytest.mark.parametrize("invalid_output", [False, True])
def test_a3_runtime_validates_raw_result_before_success_message_and_never_replays_write(tmp_path, invalid_output):
    toolkit = A3RetailLikeToolkit(invalid_output=invalid_output)
    environment = Environment(domain_name="a3-test", policy="policy", tools=toolkit)
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path, environment_factory=lambda: environment,
    ).create(
        EpisodeIdentity(run_id="a3-test", task_id="1", trial_index=0, trial_seed=300, episode_attempt=1),
        a3_recovery=True,
    )
    ctx = harness.context_for_turn(turn_index=2, requestor=Requestor.ASSISTANT)
    call = SimpleNamespace(id="write-1", name="cancel_pending_order", arguments={"order_id":"#W1", "reason":"ordered by mistake"}, requestor="assistant")
    bridge = HarnessRuntimeBridge(harness)
    if invalid_output:
        with pytest.raises(A3QuarantineRequired):
            bridge.execute_assistant_call(call, context=ctx)
        assert toolkit.db.write_calls == 1
        lifecycle = harness.lifecycle_store.read()
        assert lifecycle.status.value == "failed_operational"
        assert lifecycle.side_effect_may_have_occurred is True
        trace = harness.lifecycle_store.trace_path.read_text(encoding="utf-8")
        assert '"phase":"quarantined"' in trace
        assert "A3_QUARANTINED_OUTPUT_INVALID" in trace
        assert not any("result_emitted" in line for line in trace.splitlines())
    else:
        message = bridge.execute_assistant_call(call, context=ctx)
        assert message.error is False
        assert toolkit.db.write_calls == 1
        assert "cancelled" in message.content

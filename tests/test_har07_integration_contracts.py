from datetime import datetime, timezone

import pytest

from tau2.harness.contracts import Requestor, ToolMessageRecord
from tau2.harness.integration_contracts import (
    EpisodeIdentity, EpisodeLifecycle, EpisodeLifecycleStore, LifecycleStatus,
    OrchestratedHarnessContext, TraceFailurePolicy,
    ParserFailureSidecar, use_harness_parser, to_native_tool_message,
)
from tau2.harness.parser import ParserNormalizer, RawToolCall
from tau2.harness.trace import TraceLoggerError

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)

def parser_failure():
    from tau2.harness.contracts import TrustedRouteContext
    return ParserNormalizer().parse((RawToolCall(provider_call_id=None, raw_tool_name=None, raw_arguments_text="{"),), run_id="r", task_id="71", trial_index=0, trial_seed=1, turn_index=0, trusted_route=TrustedRouteContext(route_id="agent", requestor=Requestor.ASSISTANT), parsed_at=NOW).failures[0]

def test_scoped_context_does_not_enable_global_generate_path():
    assert use_harness_parser(None) is False
    assert use_harness_parser(OrchestratedHarnessContext(run_id="r", task_id="71", trial_index=0, trial_seed=1, turn_index=0, requestor=Requestor.ASSISTANT)) is True

def test_parser_failure_bridge_is_sidecar_only_and_safe_envelope():
    failure = parser_failure()
    context = OrchestratedHarnessContext(run_id="r", task_id="71", trial_index=0, trial_seed=1, turn_index=0, requestor=Requestor.ASSISTANT)
    sidecar = ParserFailureSidecar(context); envelope = sidecar.register(failure)
    assert envelope.id == failure.synthetic_call_id and envelope.arguments == {}
    assert sidecar.consume(envelope.id, context) is failure
    assert "raw_arguments_text" not in envelope.model_dump_json()


def test_parser_failure_sidecar_rejects_another_episode_or_turn():
    failure = parser_failure()
    other_context = OrchestratedHarnessContext(run_id="r", task_id="71", trial_index=0, trial_seed=1, turn_index=1, requestor=Requestor.ASSISTANT)
    with pytest.raises(ValueError, match="episode turn context"):
        ParserFailureSidecar(other_context).register(failure)

@pytest.mark.parametrize("record", [
    ToolMessageRecord(id="original", requestor=Requestor.ASSISTANT, content="ok", error=False),
    ToolMessageRecord(id="synthetic", requestor=Requestor.ASSISTANT, content="Tool call rejected: PARSE_MALFORMED_JSON.", error=True),
    ToolMessageRecord(id="validation", requestor=Requestor.ASSISTANT, content="Tool call rejected: SCHEMA_EXTRA_ARGUMENT.", error=True),
    ToolMessageRecord(id="native-exception", requestor=Requestor.USER, content="Tool call failed safely.", error=True),
])
def test_native_tool_message_adapter_parity(record):
    native = to_native_tool_message(record)
    assert (native.id, native.role, native.content, native.requestor, native.error) == (record.id, "tool", record.content, record.requestor.value, record.error)

def test_closed_skip_incomplete_rerun_and_mismatch_fail_fast(tmp_path):
    ident = EpisodeIdentity(run_id="r", task_id="71", trial_index=0, trial_seed=1, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, ident); store.open(); store.trace_path.write_bytes(b'{"event":1}\n')
    rerun = store.prepare_resume()
    assert rerun is not None and rerun.episode_attempt == 2
    assert store.read().status == LifecycleStatus.ABORTED
    rerun_store = EpisodeLifecycleStore(tmp_path, rerun); rerun_store.open(); rerun_store.trace_path.write_bytes(b'{"event":2}\n')
    rerun_store.close(); assert rerun_store.prepare_resume() is None
    rerun_store.trace_path.write_bytes(b'{"tampered":true}\n')
    with pytest.raises(ValueError, match="mismatch"): rerun_store.prepare_resume()


@pytest.mark.parametrize(
    ("policy", "expected_status", "side_effect"),
    [
        (TraceFailurePolicy.before_dispatch, LifecycleStatus.FAILED_BEFORE_DISPATCH, False),
        (TraceFailurePolicy.after_dispatch, LifecycleStatus.FAILED_AFTER_DISPATCH, True),
    ],
)
def test_trace_failure_policy_is_fail_fast_and_records_side_effect_boundary(tmp_path, policy, expected_status, side_effect):
    ident = EpisodeIdentity(run_id="r", task_id="71", trial_index=0, trial_seed=1, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, ident); store.open()
    with pytest.raises(TraceLoggerError):
        policy(store, TraceLoggerError("write failed"))
    lifecycle = store.read()
    assert lifecycle.status == expected_status
    assert lifecycle.side_effect_may_have_occurred is side_effect


def test_lifecycle_trace_filename_mismatch_fails_fast(tmp_path):
    ident = EpisodeIdentity(run_id="r", task_id="71", trial_index=0, trial_seed=1, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, ident)
    store.write(EpisodeLifecycle(identity=ident, status=LifecycleStatus.OPEN, trace_filename="wrong.jsonl"))
    with pytest.raises(ValueError, match="trace filename mismatch"):
        store.read()

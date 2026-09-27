"""No-network contracts for the provider-only empty-response probe."""

from scripts.run_provider_empty_diagnostic import probe_messages
from tau2.harness.retry import ProviderGenerationRetryController, ProviderInvocationResult, ProviderRetrySidecar, RetryAttempt

import json


def test_probe_nonce_makes_independent_calls_distinct_without_task_data():
    first = probe_messages(20, profile="tools-short", call_index=1)
    second = probe_messages(20, profile="tools-short", call_index=2)
    assert first[1].content != second[1].content
    assert "tools-short-1" in first[1].content
    assert "tools-short-2" in second[1].content
    assert "Retail" not in first[1].content


def test_sidecar_keeps_only_hash_for_request_fingerprint(tmp_path):
    sidecar = ProviderRetrySidecar(tmp_path / "provider-retry.jsonl", generation_id="probe-g1", turn_index=1, episode_attempt=1)
    sidecar.record(RetryAttempt(1, None, False, 0.0, telemetry={"request_fingerprint_sha256": "a" * 64, "prompt": "must-not-persist"}))
    row = json.loads(sidecar.path.read_text(encoding="utf-8"))
    assert row["request_fingerprint_sha256"] == "a" * 64
    assert "prompt" not in row


def test_controller_merges_safe_diagnostic_context_into_terminal_event(tmp_path):
    sidecar = ProviderRetrySidecar(tmp_path / "provider-retry.jsonl", generation_id="probe-g1", turn_index=1, episode_attempt=1)
    controller = ProviderGenerationRetryController(
        observer=sidecar.record,
        diagnostic_context={"request_fingerprint_sha256": "b" * 64},
    )
    controller.execute(lambda: ProviderInvocationResult(value="ok", telemetry={"response_type": "mock"}))
    row = json.loads(sidecar.path.read_text(encoding="utf-8"))
    assert row["request_fingerprint_sha256"] == "b" * 64

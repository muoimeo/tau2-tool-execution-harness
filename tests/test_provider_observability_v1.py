"""Deterministic Provider Observability v1 contracts; no gateway."""

import hashlib
import json

from tau2.harness.integration_contracts import EpisodeIdentity, EpisodeLifecycleStore, LifecycleStatus
from tau2.harness.provider_observability import write_unsafe_provider_audit_packet
from tau2.harness.retry import (
    ProviderFailureKind,
    ProviderGenerationRetryController,
    ProviderRetrySidecar,
    RetryAttempt,
    canonical_provider_request_fingerprint,
)


def test_request_fingerprint_is_deterministic_sensitive_and_one_way():
    first = {"model": "m", "messages": [{"role": "user", "content": "private prompt"}], "tools": []}
    same = {"tools": [], "messages": [{"content": "private prompt", "role": "user"}], "model": "m"}
    changed = {**first, "messages": [{"role": "user", "content": "different"}]}
    a = canonical_provider_request_fingerprint(first)
    assert a == canonical_provider_request_fingerprint(same)
    assert a != canonical_provider_request_fingerprint(changed)
    assert len(a) == 64 and "private prompt" not in a


def test_retry_same_generation_keeps_fingerprint(tmp_path):
    sidecar = ProviderRetrySidecar(tmp_path / "provider.jsonl", generation_id="g1", turn_index=7, episode_attempt=1)
    calls = [0]
    fingerprint = "c" * 64
    controller = ProviderGenerationRetryController(observer=sidecar.record, sleeper=lambda _: None, diagnostic_context={"task_id": "23", "trial": 0, "request_fingerprint_sha256": fingerprint})
    def operation():
        calls[0] += 1
        if calls[0] < 3:
            from tau2.harness.retry import ProviderGenerationError
            raise ProviderGenerationError(ProviderFailureKind.TIMEOUT)
        return "ok"
    controller.execute(operation)
    rows = [json.loads(line) for line in sidecar.path.read_text().splitlines()]
    assert [row["request_fingerprint_sha256"] for row in rows] == [fingerprint] * 3
    assert [row["task_id"] for row in rows] == ["23"] * 3
    assert [row["trial"] for row in rows] == [0] * 3


def test_unsafe_packet_is_redacted_integrity_bound_and_never_authorizes(tmp_path):
    identity = EpisodeIdentity(run_id="r", task_id="23", trial_index=0, trial_seed=300, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, identity)
    opened = store.open()
    rows = [
        {"sequence": 0, "phase": "dispatch_completed", "native_tool_type": "read", "tool_name": "get_order_details", "state_hash_before": "a" * 64, "state_hash_after": "a" * 64},
        {"sequence": 1, "phase": "dispatch_completed", "native_tool_type": "write", "tool_name": "exchange_delivered_order_items", "state_hash_before": "a" * 64, "state_hash_after": "b" * 64},
    ]
    store.trace_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id=f"{identity.filename_stem()}-g0001", turn_index=5, episode_attempt=1)
    for attempt in range(1, 4):
        sidecar.record(RetryAttempt(attempt, ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE, attempt < 3, 1.0 if attempt == 1 else 2.0 if attempt == 2 else 0.0, telemetry={"request_fingerprint_sha256": "c" * 64, "completion_id": "id", "timestamp_start": "2026-01-01T00:00:00+00:00", "timestamp_end": "2026-01-01T00:00:01+00:00"}))
    store.terminalize(opened, status=LifecycleStatus.FAILED_OPERATIONAL, outcome="failed", error="exhausted", side_effect=True)
    path = write_unsafe_provider_audit_packet(tmp_path, identity, failure_code="PROVIDER_EMPTY_ASSISTANT_MESSAGE", domain="retail")
    packet = json.loads(path.read_text())
    assert packet["write_dispatch_completed_count"] == 1
    assert packet["write_tool_names"] == ["exchange_delivered_order_items"]
    assert packet["persistence"]["fresh_environment_capability"] is True
    # The packet must bind to the exact lifecycle bytes written by the
    # terminalization that precedes packet creation.  This guards against a
    # stale-hash/order regression without mutating the lifecycle afterward.
    lifecycle_sha = hashlib.sha256(store.lifecycle_path.read_bytes()).hexdigest()
    assert packet["integrity"]["lifecycle_sha256"] == lifecycle_sha
    assert packet["integrity"]["lifecycle_sha256"] == hashlib.sha256(
        store.lifecycle_path.read_bytes()
    ).hexdigest()
    assert packet["integrity"]["provider_sidecar_snapshot_matches"] is True
    assert packet["suggested_authorization_identity"] == "23:0:1"
    assert packet["authorization_granted"] is False
    assert "arguments" not in path.read_text() and "private" not in path.read_text()

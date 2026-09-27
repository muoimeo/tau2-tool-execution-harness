"""Provider Observability v1 sidecar analysis and unsafe audit packets."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tau2.harness.a1.contracts import canonical_json_bytes
from tau2.harness.a1.integration_contracts import EpisodeIdentity, EpisodeLifecycleStore


SCHEMA_VERSION = "provider-observability/v1"
UNSAFE_AUDIT_SCHEMA = "unsafe-provider-failure-audit/v1"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_unsafe_provider_audit_packet(
    trace_root: Path,
    identity: EpisodeIdentity,
    *,
    failure_code: str,
    domain: str,
) -> Path:
    """Write a redacted deterministic summary after unsafe terminalization."""
    store = EpisodeLifecycleStore(trace_root, identity)
    lifecycle_raw = store.lifecycle_path.read_bytes()
    lifecycle = store.read()
    trace_raw = store.trace_path.read_bytes()
    provider_raw = store.provider_retry_path.read_bytes()
    trace_rows = [json.loads(line) for line in trace_raw.splitlines()]
    provider_rows = [json.loads(line) for line in provider_raw.splitlines()]
    completed = [row for row in trace_rows if row.get("phase") == "dispatch_completed"]
    writes = [row for row in completed if row.get("native_tool_type") == "write"]
    read_count = sum(row.get("native_tool_type") == "read" for row in completed)
    persistence = {
        "classification": "episode_scoped_in_memory_db_loaded_from_fixture" if domain == "retail" else "unknown",
        "fresh_environment_capability": True if domain == "retail" else None,
    }
    packet = {
        "schema_version": UNSAFE_AUDIT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity.model_dump(mode="json"),
        "failure_code": failure_code,
        "read_dispatch_completed_count": read_count,
        "write_dispatch_completed_count": len(writes),
        "write_tool_names": sorted({row.get("tool_name") for row in writes if row.get("tool_name")}),
        "write_state_transitions": [
            {"tool_name": row.get("tool_name"), "state_hash_before": row.get("state_hash_before"), "state_hash_after": row.get("state_hash_after")}
            for row in writes
        ],
        "persistence": persistence,
        "provider_request_fingerprints": [row.get("request_fingerprint_sha256") for row in provider_rows if row.get("request_fingerprint_sha256")],
        "completion_ids": [row.get("completion_id") for row in provider_rows if row.get("completion_id")],
        "retry_timing": [
            {key: row.get(key) for key in ("generation_id", "provider_attempt", "timestamp_start", "timestamp_end", "latency_ms", "backoff_seconds", "outcome", "failure_code")}
            for row in provider_rows
        ],
        "integrity": {
            "lifecycle_sha256": _sha256(lifecycle_raw),
            "lifecycle_status": lifecycle.status.value,
            "provider_sidecar_sha256": _sha256(provider_raw),
            "provider_sidecar_snapshot_matches": lifecycle.provider_retry_sha256 == _sha256(provider_raw) and lifecycle.provider_retry_event_count == len(provider_rows),
            "provider_sequence_contiguous": [row.get("sequence") for row in provider_rows] == list(range(1, len(provider_rows) + 1)),
            "tool_trace_sha256": _sha256(trace_raw),
            "tool_trace_sequence_contiguous": [row.get("sequence") for row in trace_rows] == list(range(len(trace_rows))),
        },
        "suggested_authorization_identity": f"{identity.task_id}:{identity.trial_index}:{identity.episode_attempt}",
        "authorization_granted": False,
        "raw_retention": "none",
    }
    path = Path(trace_root) / f"{identity.filename_stem()}.unsafe-audit.json"
    temporary = path.with_suffix(".tmp")
    data = canonical_json_bytes(packet)
    with temporary.open("wb") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path

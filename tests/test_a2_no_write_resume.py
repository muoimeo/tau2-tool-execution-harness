"""Offline regression for explicit rerun of conservatively marked no-WRITE failures."""

import pytest

from tau2.harness.a1.integration_contracts import (
    EpisodeIdentity,
    EpisodeLifecycleStore,
    LifecycleStatus,
)


def _failed_store(tmp_path, *, trace: str = ""):
    identity = EpisodeIdentity(
        run_id="a2", task_id="20", trial_index=0, trial_seed=626729,
        episode_attempt=1,
    )
    store = EpisodeLifecycleStore(tmp_path, identity, max_unsafe_manual_resumes=2)
    opened = store.open()
    store.trace_path.write_text(trace, encoding="utf-8")
    store.terminalize(
        opened,
        status=LifecycleStatus.FAILED_OPERATIONAL,
        outcome="failed",
        error="RateLimitError",
        side_effect=True,  # historical generic handler's conservative flag
    )
    return store


def test_no_write_authorization_creates_fresh_attempt_without_mutating_attempt_one(tmp_path):
    store = _failed_store(tmp_path)
    lifecycle_before = store.lifecycle_path.read_bytes()
    trace_before = store.trace_path.read_bytes()

    next_identity = store.prepare_resume(no_write_authorization="20:0:1")

    assert next_identity is not None and next_identity.episode_attempt == 2
    assert store.lifecycle_path.read_bytes() == lifecycle_before
    assert store.trace_path.read_bytes() == trace_before
    audit = store.manual_resume_audit_path.read_text(encoding="utf-8")
    assert '"outcome":"authorized_no_write_new_attempt"' in audit
    assert '"evidence_no_completed_write":true' in audit


def test_no_write_authorization_fails_closed_when_trace_contains_completed_write(tmp_path):
    trace = '{"sequence":0,"phase":"dispatch_completed","native_tool_type":"write"}\n'
    store = _failed_store(tmp_path, trace=trace)
    lifecycle_before = store.lifecycle_path.read_bytes()

    with pytest.raises(ValueError, match="forbidden after completed WRITE"):
        store.prepare_resume(no_write_authorization="20:0:1")

    assert store.lifecycle_path.read_bytes() == lifecycle_before
    assert not store.manual_resume_audit_path.exists()

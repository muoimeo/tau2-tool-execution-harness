"""Offline checks for append-only wrapper-level A2 resume auditing."""

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "a2_resume_audit.ps1"
WRAPPER = ROOT / "scripts" / "resume_a2_retail_stratified58_1trial_v2.cmd"


def _audit(mode: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(AUDIT_SCRIPT),
        "-Mode",
        mode,
    ]
    for name, value in kwargs.items():
        command.extend([f"-{name}", str(value)])
    return subprocess.run(command, check=True, text=True, capture_output=True)


def _reserve(tmp_path: Path, authorization: str = "") -> tuple[int, str, Path, str]:
    result = _audit(
        "reserve-invocation",
        Ledger=tmp_path / "resume-attempts.log",
        LogDirectory=tmp_path / "logs",
        RunRoot=tmp_path,
        CommandFile="scripts/_run_a2_retail_stratified58_4trials_once.cmd",
        Authorization=authorization,
    )
    sequence, invocation_id, log_path, started_at = result.stdout.strip().split("|", 3)
    return int(sequence), invocation_id, Path(log_path), started_at


def _finish(tmp_path: Path, reservation: tuple[int, str, Path, str], outcome: str) -> None:
    sequence, invocation_id, log_path, started_at = reservation
    _audit(
        "finish-invocation",
        Ledger=tmp_path / "resume-attempts.log",
        Sequence=sequence,
        InvocationId=invocation_id,
        StartedAt=started_at,
        ExitCode=0,
        Outcome=outcome,
        RunLog=log_path,
    )


def test_a2_wrapper_invocation_reservations_are_append_only_and_crash_safe(tmp_path: Path):
    """Three wrapper reservations never reuse a log or sequence after a crash."""
    lifecycle = tmp_path / "traces" / "task-1.lifecycle.json"
    lifecycle.parent.mkdir()
    lifecycle.write_bytes(b'{"immutable":"lifecycle"}\n')
    results = tmp_path / "results.json"
    results.write_bytes(b'{"immutable":"results"}\n')
    before = {path: path.read_bytes() for path in (lifecycle, results)}

    first = _reserve(tmp_path, authorization="78:0:1")
    first[2].write_text("first invocation output\n", encoding="utf-8")
    _finish(tmp_path, first, "safe-failure-continued")

    second = _reserve(tmp_path)
    second[2].write_text("crashed before terminal ledger record\n", encoding="utf-8")
    # Simulate process death: do not write a finished record for sequence 2.

    third = _reserve(tmp_path)
    third[2].write_text("third invocation output\n", encoding="utf-8")
    _finish(tmp_path, third, "completed")

    assert [first[0], second[0], third[0]] == [1, 2, 3]
    assert [first[1], second[1], third[1]] == [
        "a2inv-000001",
        "a2inv-000002",
        "a2inv-000003",
    ]
    assert len({first[2], second[2], third[2]}) == 3
    assert first[2].read_text(encoding="utf-8") == "first invocation output\n"
    assert second[2].read_text(encoding="utf-8") == "crashed before terminal ledger record\n"

    records = [
        json.loads(line)
        for line in (tmp_path / "resume-attempts.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["event"], row["sequence"]) for row in records] == [
        ("started", 1),
        ("finished", 1),
        ("started", 2),
        ("started", 3),
        ("finished", 3),
    ]
    assert all(row["invocation_id"] == "a2inv-000001" for row in records if row.get("authorization"))
    assert records[-1]["outcome"] == "completed"
    assert records[-1]["log_filename"] == "resume-invocation-000003.log"
    assert {path: path.read_bytes() for path in (lifecycle, results)} == before


def test_a2_wrapper_creates_distinct_append_only_invocation_logs(tmp_path: Path):
    """The actual CMD wrapper reserves a new audit ID even for a closed root."""
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "results.json").write_text(
        json.dumps({"simulations": [{"task_id": "1", "trial": 0, "seed": 300}]}),
        encoding="utf-8",
    )
    environment = os.environ | {
        "A2_ARTIFACT_DIR": str(tmp_path),
        "A2_EXPECTED_RESULTS": "1",
        # Completion is detected before the one-shot command is invoked.
        "A2_RUN_COMMAND": "does-not-run.cmd",
    }
    for _ in range(3):
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", str(WRAPPER)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    logs = sorted((tmp_path / "logs").glob("resume-invocation-*.log"))
    assert [path.name for path in logs] == [
        "resume-invocation-000001.log",
        "resume-invocation-000002.log",
        "resume-invocation-000003.log",
    ]
    records = [
        json.loads(line)
        for line in (tmp_path / "resume-attempts.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["event"], row["sequence"]) for row in records] == [
        ("started", 1),
        ("finished", 1),
        ("started", 2),
        ("finished", 2),
        ("started", 3),
        ("finished", 3),
    ]
    assert all(row.get("outcome") == "completed" for row in records if row["event"] == "finished")


def test_a2_wrapper_checks_only_latest_unsafe_attempt_after_prior_authorization(tmp_path: Path):
    """An authorized attempt is superseded, not required to close before retrying again."""
    traces = tmp_path / "traces"
    traces.mkdir()
    for attempt in (1, 2):
        (traces / f"task-23__trial-000__seed-300__attempt-00{attempt}.lifecycle.json").write_text(
            json.dumps(
                {
                    "identity": {"task_id": "23", "trial_index": 0, "episode_attempt": attempt},
                    "status": "failed_operational",
                    "side_effect_may_have_occurred": True,
                }
            ),
            encoding="utf-8",
        )
    (traces / "manual-unsafe-resume-audit.jsonl").write_text(
        json.dumps({"authorization": "23:0:1", "outcome": "authorized_new_attempt"}) + "\n",
        encoding="utf-8",
    )

    pending = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(AUDIT_SCRIPT),
            "-Mode", "check-unresolved-unsafe", "-TraceRoot", str(traces),
        ],
        text=True,
        capture_output=True,
    )
    assert pending.returncode == 1
    assert "23:0:2" in pending.stdout
    assert "23:0:1" not in pending.stdout

    allowed = _audit(
        "check-unresolved-unsafe", TraceRoot=traces, Authorization="23:0:2"
    )
    assert "23:0:2" in allowed.stdout


def test_a2_wrapper_accounts_for_budget_exhausted_logical_episode(tmp_path: Path):
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "task-78__trial-000__seed-300__attempt-003.lifecycle.json").write_text(
        json.dumps({
            "identity": {"task_id": "78", "trial_index": 0, "episode_attempt": 3},
            "status": "failed_operational",
            "side_effect_may_have_occurred": True,
        }),
        encoding="utf-8",
    )
    (traces / "manual-unsafe-resume-audit.jsonl").write_text(
        "\n".join(json.dumps({
            "task_id": "78", "trial_index": 0,
            "authorization": f"78:0:{attempt}",
            "outcome": "authorized_new_attempt",
        }) for attempt in (1, 2)) + "\n",
        encoding="utf-8",
    )
    skip = _audit(
        "get-exhausted-unsafe", TraceRoot=traces, MaxUnsafeManualResumes=2
    )
    assert skip.stdout.strip() == "78:0"

    results = tmp_path / "results.json"
    results.write_text(
        json.dumps({"simulations": [{"task_id": "other", "trial": 0, "seed": 300}]}),
        encoding="utf-8",
    )
    complete = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(AUDIT_SCRIPT),
            "-Mode", "check-complete", "-Results", str(results), "-TraceRoot", str(traces),
            "-ExpectedResultCount", "2", "-MaxUnsafeManualResumes", "2",
        ],
        text=True, capture_output=True,
    )
    assert complete.returncode == 0


def test_tee_wrapper_does_not_double_expand_batch_for_variables(tmp_path: Path):
    """The PowerShell tee helper must not corrupt ``%%A`` through ``call``."""
    command = tmp_path / "loop.cmd"
    command.write_text(
        "@echo off\r\n"
        "setlocal EnableDelayedExpansion\r\n"
        "for %%A in (first second) do echo token=%%~A\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    run_log = tmp_path / "tee.log"
    tee = ROOT / "scripts" / "_tee_cmd_output.ps1"
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tee),
            "-CommandFile", str(command), "-RunLog", str(run_log),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "token=first" in result.stdout
    assert "token=second" in result.stdout
    assert "batch-parameter substitution is invalid" not in result.stdout

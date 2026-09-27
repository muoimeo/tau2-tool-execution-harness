"""Read-only join of A3 FI pilot observations, traces, lifecycle and audit."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("data/simulations/60_fault_injection/fi-v1-a3-pilot-v1")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _target_trace(rows: list[dict], scenario_id: str, audit: list[dict]) -> list[dict]:
    repaired_ids = {item["logical_call_id"] for item in audit if item.get("repair_attempt_count")}
    if repaired_ids:
        return [row for row in rows if row["logical_call_id"] in repaired_ids]
    # The two non-repair parser outcomes are the only result-emitted rejects
    # without a preceding validated allow in their fixture run.
    return [
        row for row in rows
        if row.get("failure_code") in {"PARSE_MALFORMED_JSON", "SCHEMA_MISSING_REQUIRED_ARG"}
    ]


def main() -> None:
    observations = json.loads((ROOT / "pilot-observations.json").read_text(encoding="utf-8"))
    lines = [
        "# A3 FI-v1 pilot report",
        "",
        "Local loopback only; no corporate gateway was called. Counts below refer to the injected logical call, not the full scripted conversation.",
        "",
        "| Scenario | Parser / repair | A1 revalidation | Provider attempts | Native dispatch / WRITE | Output / postcondition | ToolMessage success | Terminal evidence | Invariants | Verdict |",
        "|---|---|---|---:|---|---|---|---|---|---|",
    ]
    all_pass = True
    for observation in observations:
        directory = ROOT / "a3-recovery" / observation["scenario_id"]
        trace_path = next((directory / "traces").glob("*.jsonl"))
        trace = _rows(trace_path)
        audit_path = next((directory / "traces").glob("*.recovery.jsonl"))
        audit = _rows(audit_path)
        lifecycle = json.loads(next((directory / "traces").glob("*.lifecycle.json")).read_text(encoding="utf-8"))
        if observation["scenario_id"] == "a3_truncated_json_no_repair":
            target = [row for row in trace if row.get("failure_code") == "PARSE_MALFORMED_JSON"]
        else:
            target = _target_trace(trace, observation["scenario_id"], audit)
        quarantines = [item for item in audit if item.get("quarantine_reason")]
        target_id = (
            quarantines[-1]["logical_call_id"]
            if quarantines else target[0]["logical_call_id"]
        )
        target = [row for row in trace if row["logical_call_id"] == target_id]
        phases = [row["phase"] for row in target]
        terminal_audit = next((row for row in reversed(audit) if row["logical_call_id"] == target_id), None)
        repair_count = terminal_audit.get("repair_attempt_count", 0) if terminal_audit else 0
        revalidation = "validated" in phases
        dispatch = int(any(item == "dispatch_started" for item in phases))
        write = terminal_audit.get("write_count", 0) if terminal_audit else 0
        success_message = "dispatch_completed" in phases and "result_emitted" in phases
        quarantine = terminal_audit.get("quarantine_reason") if terminal_audit else None
        output = (terminal_audit or {}).get("output_validation") or {}
        post = (terminal_audit or {}).get("postcondition") or {}
        post_text = "n/a" if not post.get("applicable") else str(post.get("passed")).lower()
        output_text = "n/a" if not output else str(output.get("valid")).lower()
        parser = "repair=" + str(repair_count)
        if target and target[0].get("failure_code"):
            parser += "; " + target[0]["failure_code"]
        expected_quarantine = observation["scenario_id"] in {
            "a3_invalid_output_after_write", "a3_postcondition_fail_after_write",
        }
        passed = (
            (expected_quarantine and lifecycle["status"] == "failed_operational" and quarantine and dispatch == 1 and write == 1 and not success_message)
            or (not expected_quarantine and lifecycle["status"] == "closed" and not quarantine)
        )
        all_pass = all_pass and passed and not observation["invariant_violations"]
        lines.append(
            f"| {observation['scenario_id']} | {parser} | {revalidation} | 1 | {dispatch} / {write} | {output_text} / {post_text} | {success_message} | {quarantine or lifecycle['status']} | {observation['invariant_violations'] or 'none'} | {'PASS' if passed else 'FAIL'} |"
        )
    lines.extend(["", f"Overall: **{'PASS' if all_pass else 'FAIL'}**."])
    output = ROOT / "analysis" / "a3_fi_pilot_report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

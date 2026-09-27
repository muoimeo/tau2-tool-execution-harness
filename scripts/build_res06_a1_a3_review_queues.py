"""Build evidence-bounded RES-06 review queues for A1, A2, and A3.

This is deliberately read-only with respect to benchmark artifacts: it reads a
completed run's results/traces and writes only an ``analysis/`` directory below
that run.  Crucially, a label from the paired A0 trial is a *hint*, never an
inherited causal label.  A harness can change the trajectory before an episode
fails, so reward=0 cannot by itself prove reasoning, simulator, evaluator, or
harness causality.

Run from the repository root:
    .\\.venv\\Scripts\\python.exe scripts\\build_res06_a1_a3_review_queues.py
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
A0_REVIEW = REPO / "data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/analysis/failure_annotations_reviewed_v1.csv"

RUNS = {
    "A1": REPO / "data/simulations/30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1",
    "A2": REPO / "data/simulations/40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability",
    "A3": REPO / "data/simulations/50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_path(root: Path) -> Path:
    direct = root / "results.json"
    nested = root / "results/results.json"
    if direct.exists():
        return direct
    if nested.exists():
        return nested
    raise FileNotFoundError(f"No results.json below {root}")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def paired_a0_labels() -> dict[tuple[str, int], list[str]]:
    labels: dict[tuple[str, int], list[str]] = defaultdict(list)
    with A0_REVIEW.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # The reward-zero episode-outcome row is the only suitable paired hint.
            if row.get("trigger") != "native_reward_zero":
                continue
            code = row.get("primary_failure_code", "")
            if code:
                labels[(str(row["task_id"]), int(row["trial_index"]))].append(code)
    return {key: sorted(set(value)) for key, value in labels.items()}


def trace_summary(root: Path, simulation: dict[str, Any]) -> dict[str, Any]:
    """Summarise event evidence without copying raw arguments/content to CSV."""
    task, trial, seed = str(simulation["task_id"]), int(simulation["trial"]), simulation["seed"]
    traces = root / "traces"
    pattern = f"task-{task}__trial-{trial:03d}__seed-{seed}__attempt-*.jsonl"
    event_files = sorted(traces.glob(pattern))
    event_count = reject_count = dispatch_completed = write_dispatch_completed = 0
    failure_codes: set[str] = set()
    for path in event_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            event_count += 1
            if event.get("decision") == "REJECT":
                reject_count += 1
            if event.get("phase") == "dispatch_completed":
                dispatch_completed += 1
                if event.get("tool_type") == "WRITE":
                    write_dispatch_completed += 1
            if event.get("failure_code"):
                failure_codes.add(str(event["failure_code"]))
    lifecycle_files = sorted(traces.glob(f"task-{task}__trial-{trial:03d}__seed-{seed}__attempt-*.lifecycle.json"))
    statuses: list[str] = []
    for path in lifecycle_files:
        try:
            statuses.append(str(json.loads(path.read_text(encoding="utf-8")).get("status")))
        except json.JSONDecodeError:
            statuses.append("INVALID_JSON")
    return {
        "trace_files": ";".join(path.name for path in event_files),
        "trace_event_count": event_count,
        "harness_reject_events": reject_count,
        "dispatch_completed_events": dispatch_completed,
        "write_dispatch_completed_events": write_dispatch_completed,
        "trace_failure_codes": ";".join(sorted(failure_codes)),
        "lifecycle_files": ";".join(path.name for path in lifecycle_files),
        "lifecycle_statuses": ";".join(statuses),
    }


FIELDS = [
    "annotation_id", "stage_run", "simulation_id", "task_id", "trial_index", "seed",
    "trigger", "reward", "db_match", "action_match", "communicate_match", "termination_reason",
    "primary_failure_code", "secondary_failure_codes", "classification_status", "stage", "source",
    "severity", "retryability", "recoverability", "expected_action", "actual_action", "final_outcome",
    "evidence_ref", "notes", "attribution_confidence", "paired_a0_label_hint", "trajectory_ref",
    "trace_files", "trace_event_count", "harness_reject_events", "dispatch_completed_events",
    "write_dispatch_completed_events", "trace_failure_codes", "lifecycle_files", "lifecycle_statuses",
]


def bool_or_na(value: Any) -> str:
    return "not_applicable" if value is None else str(bool(value)).lower()


def build(stage: str, root: Path, a0_hints: dict[tuple[str, int], list[str]]) -> dict[str, Any]:
    results_file = result_path(root)
    result = json.loads(results_file.read_text(encoding="utf-8"))
    simulations = result["simulations"]
    tasks = {str(task["id"]): task for task in result["tasks"]}
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for sim in simulations:
        info = sim["reward_info"]
        reward = info["reward"]
        tool_errors = [
            message for message in sim["messages"]
            if message.get("role") == "tool" and message.get("error") is True
        ]
        if reward != 0.0 and not tool_errors:
            continue
        task_id, trial = str(sim["task_id"]), int(sim["trial"])
        action_checks = info.get("action_checks") or []
        action_match = None if not action_checks else all(check.get("action_match") for check in action_checks)
        communication = info.get("communicate_checks")
        communicate_match = None if communication is None else all(communication)
        triggers = []
        if reward == 0.0:
            triggers.append("evaluator_reward_zero")
        if tool_errors:
            triggers.append("native_tool_message_error")
        trace = trace_summary(root, sim)
        hints = ";".join(a0_hints.get((task_id, trial), []))
        task = tasks.get(task_id, {})
        user_scenario = task.get("user_scenario", {}).get("instructions", {})
        task_ref = f"results.json:tasks[id={task_id}]; reason_for_call={user_scenario.get('reason_for_call', '')[:120]}"
        rows.append({
            "annotation_id": f"{stage.lower()}:{sim['id']}:outcome-or-tool-trigger",
            "stage_run": stage, "simulation_id": sim["id"], "task_id": task_id,
            "trial_index": trial, "seed": sim["seed"], "trigger": ";".join(triggers),
            "reward": reward, "db_match": bool_or_na(info.get("db_check", {}).get("db_match")),
            "action_match": bool_or_na(action_match), "communicate_match": bool_or_na(communicate_match),
            "termination_reason": sim.get("termination_reason"),
            "primary_failure_code": "", "secondary_failure_codes": "",
            "classification_status": "pending_manual_review", "stage": "not_attributed",
            "source": "not_attributed", "severity": "not_attributed", "retryability": "not_attributed",
            "recoverability": "not_attributed", "expected_action": "",
            "actual_action": "", "final_outcome": "failed" if reward == 0.0 else "recovered_or_completed",
            "evidence_ref": task_ref,
            "notes": (
                "Do not infer a causal code from reward/tool-error alone. Review task criteria, full messages, "
                "tool calls/results, and stage trace/lifecycle evidence. Paired A0 label is a reading hint only."
            ),
            "attribution_confidence": "unassigned", "paired_a0_label_hint": hints or "none",
            "trajectory_ref": f"{results_file.relative_to(REPO)} simulation_id={sim['id']}", **trace,
        })
        reason_counts["reward_zero"] += reward == 0.0
        reason_counts["tool_error_trigger"] += bool(tool_errors)

    queue = analysis / "res06_failure_annotations_queue.csv"
    write_csv(queue, rows, FIELDS)
    summary = {
        "schema_version": "RES-06-cross-stage-review-queue-v1",
        "stage": stage,
        "read_only_inputs": {"results": str(results_file.relative_to(REPO)), "results_sha256": sha256(results_file)},
        "serialized_episodes": len(simulations),
        "reward_zero_episodes": sum(row["reward"] == 0.0 for row in rows),
        "tool_error_trigger_episodes": sum("native_tool_message_error" in row["trigger"] for row in rows),
        "review_queue_rows": len(rows),
        "classification_rule": "All causal fields are intentionally blank pending manual trajectory review.",
        "paired_a0_hint_rule": "A paired A0 label is a non-causal reading hint; it must not be copied without matching evidence.",
    }
    (analysis / "res06_review_queue_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (analysis / "res06_review_protocol.md").write_text(
        f"# RES-06 manual review protocol — {stage}\n\n"
        f"- Queue: `{queue.name}`; {len(rows)} episode-level triggers.\n"
        "- Read task/evaluation criteria, complete trajectory, tool result/error, trace, lifecycle, and provider sidecar (A2/A3) before assigning a code.\n"
        "- `paired_a0_label_hint` is never evidence of the same cause; it only tells the reviewer which A0 episode to compare.\n"
        "- Do not label `reward=0` as harness failure unless trace proves a harness/runtime causal path.\n"
        "- Keep provider/lifecycle failures separate from completed benchmark outcome rows.\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    hints = paired_a0_labels()
    outputs = {stage: build(stage, root, hints) for stage, root in RUNS.items()}
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()

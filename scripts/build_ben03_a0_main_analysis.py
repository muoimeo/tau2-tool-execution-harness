"""Build read-only BEN-03 artifacts for the frozen A0-Main Retail-58 run.

This script never writes to native results, telemetry, ledger, taskset, or manifest.
Failure rows without sufficient trace evidence deliberately remain pending manual review.
"""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1")
OUT = ROOT / "analysis"
WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
    "transfer_to_human_agents",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def value_or_na(value):
    return "not_applicable" if value is None else value


def tool_results_by_id(messages: list[dict]) -> dict[str, dict]:
    return {
        str(message["id"]): message
        for message in messages
        if message.get("role") == "tool" and message.get("id") is not None
    }


def parse_attempt_log(path: Path) -> dict:
    if not path.exists():
        return {"present": False, "start_events": 0, "end_events": 0}
    starts = ends = 0
    exit_codes = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if " start attempt=" in line:
            starts += 1
        match = re.search(r" end attempt=\d+ exit_code=(\d+)", line)
        if match:
            ends += 1
            exit_codes[match.group(1)] += 1
    return {
        "present": True,
        "start_events": starts,
        "end_events": ends,
        "ended_by_exit_code": dict(sorted(exit_codes.items())),
        "starts_without_recorded_end": max(starts - ends, 0),
        "classification": "external_operational_observation_not_episode_metric",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "results": ROOT / "results/results.json",
        "ledger": ROOT / "ledger/scheduled-runs.json",
        "taskset": ROOT / "taskset.json",
        "manifest": ROOT / "manifest.yaml",
        "telemetry_summary": ROOT / "telemetry/a0-main/summary.json",
        "telemetry_episodes": ROOT / "telemetry/a0-main/episodes.json",
        "telemetry_tool_events": ROOT / "telemetry/a0-main/tool-events.json",
        "resume_attempts": ROOT / "resume-attempts.log",
    }
    for name, path in files.items():
        if name != "resume_attempts" and not path.exists():
            raise FileNotFoundError(path)

    results = json.loads(files["results"].read_text(encoding="utf-8"))
    ledger = json.loads(files["ledger"].read_text(encoding="utf-8"))
    telemetry = json.loads(files["telemetry_summary"].read_text(encoding="utf-8"))
    planned = {
        (str(row["task_id"]), row["trial"], row["trial_seed"]): row
        for row in ledger["runs"]
    }

    episodes, calls, annotations = [], [], []
    per_task = defaultdict(list)
    seen = Counter()

    for simulation in sorted(
        results["simulations"], key=lambda s: (s["trial"], int(s["task_id"]))
    ):
        task_id, trial, seed = str(simulation["task_id"]), simulation["trial"], simulation["seed"]
        key = (task_id, trial, seed)
        if key not in planned:
            raise ValueError(f"Unexpected serialized tuple: {key}")
        seen[key] += 1
        run = planned[key]
        reward_info = simulation["reward_info"]
        action_checks = reward_info.get("action_checks") or []
        communicate_checks = reward_info.get("communicate_checks")
        messages = simulation["messages"]
        results_by_id = tool_results_by_id(messages)
        usages = defaultdict(int)
        for message in messages:
            for metric, count in (message.get("usage") or {}).items():
                usages[f"{message.get('role')}_{metric}"] += count or 0

        call_count = error_count = write_count = 0
        tool_error_rows = []
        for message_index, message in enumerate(messages):
            for call_index, call in enumerate(message.get("tool_calls") or []):
                call_count += 1
                name = call["name"]
                tool_type = "WRITE" if name in WRITE_TOOLS else "READ"
                write_count += tool_type == "WRITE"
                call_id = str(call.get("id") or f"{run['run_id']}:turn{message_index}:call{call_index}")
                result = results_by_id.get(call_id)
                tool_error = None if result is None else result.get("error")
                error_count += tool_error is True
                row = {
                    "run_id": run["run_id"], "task_id": task_id, "trial_index": trial,
                    "trial_seed": seed, "turn_index": message_index,
                    "logical_call_id": call_id, "attempt_index": 0,
                    "tool_name": name, "tool_type": tool_type,
                    "arguments_json": json.dumps(call.get("arguments", {}), sort_keys=True),
                    "tool_result_observed": result is not None,
                    "tool_result_error": value_or_na(tool_error),
                    "tool_result_content": "not_exported_to_csv", "dispatch_attempted": True,
                    "native_parse_status": "parsed_tool_call",
                    "raw_tool_name": "not_observed_after_native_parse",
                    "raw_arguments_text": "not_observed_after_native_parse",
                    "schema_validation": "not_evaluated_native",
                    "exception_type": "not_persisted_native",
                    "per_call_latency_ms": "not_persisted_native",
                    "harness_decision": "not_applicable_A0",
                    "evidence_class": "native_or_derived",
                }
                calls.append(row)
                if tool_error is True:
                    tool_error_rows.append(row)

        reward = reward_info["reward"]
        db_match = reward_info["db_check"]["db_match"]
        action_match = "not_applicable" if not action_checks else all(
            check["action_match"] for check in action_checks
        )
        communicate_match = (
            "not_applicable"
            if communicate_checks is None
            else all(communicate_checks)
        )
        episode = {
            "run_id": run["run_id"], "config_id": "EXP-01-A0-Main-retail-stratified-58-v1",
            "benchmark": "tau2-bench-v0.1.3", "domain": "retail",
            "task_id": task_id, "trial_index": trial, "trial_seed": seed,
            "harness_mode": "off", "fault_profile": "clean",
            "episode_completed": True, "evaluator_reached": True,
            "termination_reason": simulation["termination_reason"], "reward": reward,
            "db_match": db_match, "action_match": action_match,
            "communicate_match": communicate_match, "pass_1": reward == 1.0,
            "duration_ms": round(simulation["duration"] * 1000, 3),
            "logical_call_count": call_count, "write_call_count": write_count,
            "tool_error_count": error_count,
            "agent_prompt_tokens": usages["assistant_prompt_tokens"],
            "agent_completion_tokens": usages["assistant_completion_tokens"],
            "user_prompt_tokens": usages["user_prompt_tokens"],
            "user_completion_tokens": usages["user_completion_tokens"],
            "agent_cost": simulation["agent_cost"], "user_cost": simulation["user_cost"],
            "evidence_class": "native_or_derived",
        }
        episodes.append(episode)
        per_task[task_id].append(episode)

        # Do not infer semantic root cause from reward=0 alone.
        if reward == 0.0:
            annotations.append({
                "annotation_id": f"{run['run_id']}:episode-outcome",
                "run_id": run["run_id"], "task_id": task_id, "trial_index": trial,
                "logical_call_id": "none", "trigger": "native_reward_zero",
                "primary_failure_code": "", "secondary_failure_codes": "",
                "classification_status": "pending_manual_review",
                "stage": "not_attributed", "source": "not_attributed",
                "severity": "not_attributed", "evidence_type": "native_result",
                "evidence_ref": "reward_info.reward=0.0",
                "notes": "Reward=0 alone cannot distinguish agent reasoning, user simulator, evaluator, or execution causes. Not counted as harness failure.",
            })
        for row in tool_error_rows:
            annotations.append({
                "annotation_id": f"{row['logical_call_id']}:tool-error",
                "run_id": run["run_id"], "task_id": task_id, "trial_index": trial,
                "logical_call_id": row["logical_call_id"], "trigger": "native_tool_message_error",
                "primary_failure_code": "", "secondary_failure_codes": "",
                "classification_status": "pending_manual_review",
                "stage": "execution_observed_cause_not_persisted", "source": "tool_or_environment_unknown",
                "severity": "not_attributed", "evidence_type": "native_trace",
                "evidence_ref": f"turn={row['turn_index']}; ToolMessage.error=true",
                "notes": "Native trace persists only ToolMessage.error, not exception type or cause. Do not label timeout, schema, harness, or model failure without trace review.",
            })

    duplicates = sum(count - 1 for count in seen.values() if count > 1)
    missing = sorted(set(planned) - set(seen))
    unexpected = sorted(set(seen) - set(planned))
    if duplicates or missing or unexpected or len(episodes) != len(planned):
        raise ValueError({"duplicates": duplicates, "missing": missing[:5], "unexpected": unexpected[:5]})

    write_csv(OUT / "episode_metrics.csv", episodes)
    write_csv(OUT / "tool_calls.csv", calls)
    write_csv(OUT / "failure_annotations.csv", annotations)

    task_metrics = {}
    for task_id, rows in sorted(per_task.items(), key=lambda item: int(item[0])):
        task_metrics[task_id] = {
            "episodes": len(rows),
            "reward_passes": sum(row["pass_1"] for row in rows),
            "reward_rate_pass1": sum(row["pass_1"] for row in rows) / len(rows),
            "db_matches": sum(row["db_match"] for row in rows),
            "db_match_rate": sum(row["db_match"] for row in rows) / len(rows),
            "pass4": all(row["pass_1"] for row in rows),
            "tool_calls": sum(row["logical_call_count"] for row in rows),
            "tool_errors": sum(row["tool_error_count"] for row in rows),
        }
    pass4_count = sum(item["pass4"] for item in task_metrics.values())
    tool_breakdown = Counter(
        (row["task_id"], row["tool_name"], row["tool_type"], str(row["tool_result_error"]))
        for row in calls
    )
    operational = parse_attempt_log(files["resume_attempts"])
    summary = {
        "schema_version": "BEN-03-A0-Main-v1",
        "classification": "derived_read_only_analysis",
        "input_sha256": {name: sha256(path) for name, path in files.items() if path.exists()},
        "reconciliation": {
            "planned_episodes": len(planned), "serialized_episodes": len(episodes),
            "unique_tuples": len(seen), "duplicate_tuples": duplicates,
            "missing_tuples": len(missing), "unexpected_tuples": len(unexpected),
        },
        "metric_denominators": {
            "pass1_episodes": len(episodes), "pass4_tasks": len(task_metrics),
            "tool_calls": len(calls), "native_tool_error_results": len(calls),
        },
        "metric_counts": {
            "reward_passes": sum(row["pass_1"] for row in episodes),
            "db_matches": sum(row["db_match"] for row in episodes),
            "pass4_tasks": pass4_count, "native_tool_error_results": sum(row["tool_error_count"] for row in episodes),
            "read_calls": sum(row["tool_type"] == "READ" for row in calls),
            "write_calls": sum(row["tool_type"] == "WRITE" for row in calls),
        },
        "metric_rates": {
            "reward_rate_pass1": sum(row["pass_1"] for row in episodes) / len(episodes),
            "db_match_rate": sum(row["db_match"] for row in episodes) / len(episodes),
            "pass4_rate": pass4_count / len(task_metrics),
        },
        "per_task": task_metrics,
        "tool_breakdown": [
            {"task_id": task, "tool_name": name, "tool_type": kind, "tool_result_error": error, "count": count}
            for (task, name, kind, error), count in sorted(tool_breakdown.items(), key=lambda item: (int(item[0][0]), item[0][1], item[0][3]))
        ],
        "telemetry": telemetry,
        "operational_resume_attempts": operational,
        "failure_annotation_queue": {
            "rows": len(annotations),
            "rule": "No RES-06A causal failure code assigned without manual trace evidence.",
        },
        "observability_limits": [
            "No raw malformed model response persisted for episodes aborted before serialization.",
            "No per-call exception type, timeout status, latency, retry lineage, state snapshot, or state diff in native artifacts.",
            "ToolMessage.error does not identify root cause by itself.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    reward_passes = summary["metric_counts"]["reward_passes"]
    db_matches = summary["metric_counts"]["db_matches"]
    lines = [
        "# BEN-03 — A0-Main Retail-58 analysis",
        "",
        "## Scope",
        "",
        "- Read-only analysis of native results, passive telemetry, frozen ledger/taskset/manifest, and external resume-attempt log.",
        f"- Reconciled **{len(episodes)}/{len(planned)}** scheduled tuples; duplicates, missing, and unexpected tuples are all zero.",
        "- Preflight is excluded. Process-level interruptions are operational evidence, not episode outcomes.",
        "",
        "## Benchmark-native and derived metrics",
        "",
        f"- Reward / pass^1: **{reward_passes}/{len(episodes)}** ({summary['metric_rates']['reward_rate_pass1']:.2%}).",
        f"- DB match: **{db_matches}/{len(episodes)}** ({summary['metric_rates']['db_match_rate']:.2%}).",
        f"- pass^4: **{pass4_count}/{len(task_metrics)}** tasks ({summary['metric_rates']['pass4_rate']:.2%}); a task passes only if all four frozen trials reward 1.",
        f"- Tool calls: **{len(calls)}** ({summary['metric_counts']['read_calls']} READ; {summary['metric_counts']['write_calls']} WRITE).",
        f"- Native ToolMessage.error results: **{summary['metric_counts']['native_tool_error_results']}**.",
        "",
        "## Per-task outcome table",
        "",
        "| Task | pass^1 | DB match | pass^4 | Tool calls | Tool errors |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for task_id, metric in task_metrics.items():
        lines.append(
            f"| {task_id} | {metric['reward_passes']}/{metric['episodes']} | {metric['db_matches']}/{metric['episodes']} | "
            f"{'yes' if metric['pass4'] else 'no'} | {metric['tool_calls']} | {metric['tool_errors']} |"
        )
    lines += [
        "",
        "## Tool-call breakdown",
        "",
        "The full task × tool × READ/WRITE × ToolMessage.error breakdown is in `summary.json` and `tool_calls.csv`.",
        "",
        "## Failure-attribution boundary",
        "",
        f"- `failure_annotations.csv` is a **manual-review queue** with {len(annotations)} native triggers (reward=0 and/or ToolMessage.error).",
        "- It intentionally leaves RES-06A causal failure codes blank until a reviewer reads the relevant trajectory and task/policy evidence.",
        "- A reward failure, simulator behaviour, evaluator diagnostic, or ToolMessage.error is not automatically a harness failure.",
        "",
        "## Operational reliability observation",
        "",
        f"- External resume log: {operational.get('start_events', 0)} starts, {operational.get('end_events', 0)} ended attempts; exit-code counts: {operational.get('ended_by_exit_code', {})}.",
        "- These process interruptions were resolved through native tau2 resume. They are excluded from benchmark reward/pass denominators but retained as evidence for later A1/A2 response-contract/retry evaluation.",
        "",
        "## Metric boundaries",
        "",
        "- Native: reward, DB check, action/communication checks, parsed tool calls, ToolMessage.error, duration, token usage, termination reason.",
        "- Derived: reconciliation, pass^k, READ/WRITE grouping, rates, tool-call aggregation, and operational-log counts.",
        "- Not measured in A0: raw malformed response, schema-oracle outcome, exception class, per-call latency, retry/repair lineage, and state snapshot/diff/postcondition.",
    ]
    (OUT / "a0_main_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

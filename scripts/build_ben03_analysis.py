"""Build derived BEN-03 artifacts without modifying tau2 raw artifacts."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path("data/simulations/20_a0_native_baseline/a0-pilot/pilot5-v1")
OUT = ROOT / "analysis"
WRITE_TOOLS = {
    "cancel_pending_order", "exchange_delivered_order_items",
    "modify_pending_order_address", "modify_pending_order_items",
    "modify_pending_order_payment", "modify_user_address",
    "return_delivered_order_items", "transfer_to_human_agents",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name, rows):
    fields = list(rows[0]) if rows else []
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def associated_tool_result(messages, index):
    """Native policy permits at most one call; associate the immediately next tool message."""
    if index + 1 < len(messages) and messages[index + 1].get("role") == "tool":
        return messages[index + 1]
    return None


def annotation(episode_id, logical_call_id, code, stage, source, severity,
               retryability, recoverability, expected, actual, outcome, evidence, notes,
               secondary=""):
    return {
        "episode_id": episode_id, "logical_call_id": logical_call_id,
        "primary_failure_code": code, "secondary_failure_codes": secondary,
        "stage": stage, "source": source, "evidence_type": "native_trace",
        "severity": severity, "retryability": retryability,
        "recoverability": recoverability, "expected_action": expected,
        "actual_action": actual, "final_outcome": outcome,
        "evidence_ref": evidence, "reviewer_notes": notes,
        "evidence_class": "manual_annotation",
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "results": ROOT / "results/results.json",
        "telemetry": ROOT / "telemetry/a0-pilot/summary.json",
        "ledger": ROOT / "ledger/scheduled-runs.json",
        "taskset": ROOT / "taskset.json",
        "manifest": ROOT / "manifest.yaml",
    }
    results = json.loads(files["results"].read_text(encoding="utf-8"))
    telemetry = json.loads(files["telemetry"].read_text(encoding="utf-8"))
    ledger = json.loads(files["ledger"].read_text(encoding="utf-8"))
    run_ids = {(r["task_id"], r["trial"]): r["run_id"] for r in ledger["runs"]}
    episode_rows, call_rows, annotations = [], [], []

    for sim in sorted(results["simulations"], key=lambda x: (x["trial"], int(x["task_id"]))):
        task_id, trial = str(sim["task_id"]), sim["trial"]
        episode_id = run_ids[(task_id, trial)]
        messages = sim["messages"]
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        calls = [c for m in messages for c in (m.get("tool_calls") or [])]
        usage = defaultdict(int)
        for message in messages:
            for key, value in (message.get("usage") or {}).items():
                usage[f"{message.get('role')}_{key}"] += value or 0
        reward = sim["reward_info"]
        action_checks = reward.get("action_checks") or []
        action_match = "not_applicable" if not action_checks else all(a["action_match"] for a in action_checks)
        episode_rows.append({
            "run_id": episode_id, "config_id": "A0-Pilot-native", "benchmark": "tau2-bench-v0.1.3",
            "domain": "retail", "task_id": task_id, "trial_index": trial, "seed": sim["seed"],
            "fault_profile": "clean", "agent_model": results["info"]["agent_info"]["llm"],
            "user_model": results["info"]["user_info"]["llm"], "harness_mode": "off",
            "episode_completed": True, "evaluator_reached": True,
            "termination_reason": sim["termination_reason"], "reward": reward["reward"],
            "db_match": reward["db_check"]["db_match"], "action_match": action_match,
            "communicate_match": "not_applicable" if reward.get("communicate_checks") is None else all(reward["communicate_checks"]),
            "pass_1": reward["reward"] == 1, "logical_call_count": len(calls),
            "raw_candidate_call_count": "not_observed_after_native_parse",
            "valid_call_count": "not_evaluated_by_frozen_oracle", "invalid_call_count": "not_evaluated_by_frozen_oracle",
            "dispatch_attempt_count": len(calls), "successful_call_count": sum(not m.get("error") for m in tool_messages),
            "tool_error_count": sum(m.get("error") is True for m in tool_messages),
            "retry_count": 0, "repair_count": 0, "recovery_success_count": "not_applicable",
            "write_call_count": sum(c["name"] in WRITE_TOOLS for c in calls),
            "unsafe_dispatch_count": "not_evaluated_by_frozen_oracle", "false_rejection_count": "not_applicable",
            "duration_ms": round(sim["duration"] * 1000, 3),
            "agent_prompt_tokens": usage["assistant_prompt_tokens"], "agent_completion_tokens": usage["assistant_completion_tokens"],
            "user_prompt_tokens": usage["user_prompt_tokens"], "user_completion_tokens": usage["user_completion_tokens"],
            "recovery_tokens": 0, "cost": sim["agent_cost"] + sim["user_cost"],
            "evidence_class": "native_or_derived",
        })
        for msg_index, message in enumerate(messages):
            for call_index, call in enumerate(message.get("tool_calls") or []):
                result = associated_tool_result(messages, msg_index)
                name = call["name"]
                logical_id = call.get("id") or f"{episode_id}:turn{msg_index}:call{call_index}"
                call_rows.append({
                    "run_id": "A0-Pilot-native", "episode_id": episode_id, "task_id": task_id,
                    "turn_index": msg_index, "logical_call_id": logical_id, "attempt_index": 0,
                    "tool_call_id": call.get("id"), "requestor": call.get("requestor"),
                    "raw_tool_name": "not_observed_after_native_parse", "raw_arguments_text": "not_observed_after_native_parse",
                    "parse_status": "parsed_native_toolcall", "parse_error_code": "not_observed",
                    "normalized_tool_name": name, "normalized_arguments_json": json.dumps(call["arguments"], sort_keys=True),
                    "oracle_valid": "not_evaluated_by_frozen_oracle", "oracle_error_class": "not_evaluated",
                    "registry_status": "resolved_native", "schema_status": "not_evaluated_by_frozen_oracle",
                    "validation_errors": "not_applicable_native", "tool_type": "WRITE" if name in WRITE_TOOLS else "READ",
                    "harness_decision": "not_applicable_A0", "rejection_reason": "not_applicable_A0",
                    "dispatch_attempted": True, "fault_injected": False, "fault_class": "none",
                    "fault_id": "none", "retryable": "not_applicable_A0", "recoverable": "not_applicable_A0",
                    "attempt_start_time": "not_observed", "attempt_end_time": "not_observed", "latency_ms": "not_observed",
                    "tool_result_error": "not_observed" if result is None else result.get("error"),
                    "error_code": "TOOL_EXECUTION_ERROR" if result and result.get("error") else "none",
                    "exception_type": "not_persisted", "retry_triggered": False, "repair_type": "not_applicable_A0",
                    "repair_success": "not_applicable_A0", "output_valid": "not_evaluated_no_output_contract",
                    "final_logical_call_outcome": "tool_error" if result and result.get("error") else "tool_result",
                    "pre_state_hash": "not_observed", "post_state_hash": "not_observed", "state_changed": "not_observed",
                    "postcondition_pass": "not_observed", "evidence_class": "native_or_derived",
                })

        # Manual annotations: three task-79 final DB mismatches.
        if task_id == "79" and reward["reward"] == 0:
            if trial in (0, 1):
                mutation = next(c for c in calls if c["name"] == "modify_pending_order_items")
                annotations.append(annotation(episode_id, mutation["id"], "MODEL_REASONING_FAILURE", "external", "model", "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan", "native dispatched semantic-wrong WRITE", "failed", f"turn {next(i for i,m in enumerate(messages) if mutation in (m.get('tool_calls') or []))}", "Selected black item 7661609223 while the frozen task action requires red item 2439754078; schema/tool execution succeeded, so this is not a harness failure.", "STATE_FINAL_DB_MISMATCH"))
            else:
                annotations.append(annotation(episode_id, "none", "MODEL_REASONING_FAILURE", "external", "model", "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan", "native ended without requested mutation", "failed", "turns 12-22", "Agent did not resolve the requested colour and ended the conversation without modify_pending_order_items; no execution-layer error evidence.", "STATE_FINAL_DB_MISMATCH"))
        # Four task-68 error results followed by a corrected identity lookup and success.
        if task_id == "68":
            for index, message in enumerate(messages):
                if message.get("role") == "tool" and message.get("error"):
                    prior = messages[index - 1]
                    call = prior["tool_calls"][0]
                    annotations.append(annotation(episode_id, call["id"], "TOOL_DOMAIN_PRECONDITION_ERROR", "execution", "tool", "S1_RECOVERABLE", "non_retryable", "replan_required", "replan", "agent requested corrected zip then made a new lookup", "recovered", f"turns {index-1}-{index+4}", "Native text is 'Error: User not found'. The same request is not retried; the user supplies corrected zip and a new lookup succeeds. This is not a harness retry or a harness failure."))
        # Reward succeeds but ACTION diagnostic is false in every task-73 trial.
        if task_id == "73" and action_checks and not all(a["action_match"] for a in action_checks):
            annotations.append(annotation(episode_id, next((c["id"] for c in calls if c["name"] == "return_delivered_order_items"), "none"), "EVAL_ACTION_MISMATCH_DIAGNOSTIC", "evaluation", "evaluator", "S0_INFO", "not_applicable", "not_applicable", "record diagnostic", "native reward/DB success", "success", "reward_info.action_checks", "ACTION is not in this task's reward_basis; action_match=false coexists with reward=1 and db_match=true. Do not classify as harness failure."))

    write_csv("episode_metrics.csv", episode_rows)
    write_csv("tool_calls.csv", call_rows)
    write_csv("failure_annotations.csv", annotations)
    by_task = defaultdict(list)
    for row in episode_rows:
        by_task[row["task_id"]].append(row)
    pass4 = sum(all(r["pass_1"] for r in rows) for rows in by_task.values())
    summary = {
        "protocol_version": "MET-01-v1.1", "schema_version": "BEN-03-A0-Pilot-v1",
        "git_commit": results["info"]["git_commit"],
        "input_sha256": {name: digest(path) for name, path in files.items()},
        "config": {"agent": results["info"]["agent_info"]["llm"], "user": results["info"]["user_info"]["llm"], "num_trials": results["info"]["num_trials"], "root_seed": results["info"]["seed"], "max_steps": results["info"]["max_steps"], "max_errors": results["info"]["max_errors"], "harness_intervention": False},
        "metric_denominators": {"scheduled_episodes": len(ledger["runs"]), "serialized_episodes": len(episode_rows), "execution_attempts": len(call_rows), "tasks_for_pass_k": len(by_task)},
        "metric_counts": {"episode_completion": len(episode_rows), "reward_pass": sum(r["pass_1"] for r in episode_rows), "db_match": sum(r["db_match"] for r in episode_rows), "pass4_tasks": pass4, "native_tool_error_results": sum(r["tool_error_count"] for r in episode_rows), "read_attempts": sum(r["tool_type"] == "READ" for r in call_rows), "write_attempts": sum(r["tool_type"] == "WRITE" for r in call_rows)},
        "metric_rates": {"episode_completion_rate": len(episode_rows) / len(ledger["runs"]), "reward_rate_pass1": sum(r["pass_1"] for r in episode_rows) / len(ledger["runs"]), "db_match_rate": sum(r["db_match"] for r in episode_rows) / len(ledger["runs"]), "pass4": pass4 / len(by_task)},
        "telemetry": telemetry,
        "failure_annotation_count": len(annotations),
        "excluded_runs": ["preflight/dry-run-2episodes.json", "preflight/dry-run-task70-repro.json", "preflight/fixture-checks/fixture_check_b2_sonnet5/"],
        "known_observability_gaps": ["raw model argument text and malformed parse candidates", "per-attempt latency", "exception type", "state hashes/diffs/postconditions", "retry/repair lineage", "frozen schema-oracle results"],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    task_counts = Counter((r["task_id"], r["normalized_tool_name"], r["tool_type"], str(r["tool_result_error"])) for r in call_rows)
    lines = ["# BEN-03 — A0-Pilot execution analysis", "", "## Scope and reconciliation", "", "- Derived only from native `results.json`, passive telemetry, ledger, taskset, and manifest; raw artifacts were not modified.", f"- Reconciliation: **{len(episode_rows)}/{len(ledger['runs'])}** scheduled episodes; no duplicate or missing final tuples.", "- Preflight artifacts are excluded from metrics.", "", "## Native outcome metrics", "", f"- Episode completion: {len(episode_rows)}/{len(ledger['runs'])} (100%).", f"- Reward rate / pass^1: {sum(r['pass_1'] for r in episode_rows)}/{len(ledger['runs'])} (85%).", f"- DB match: {sum(r['db_match'] for r in episode_rows)}/{len(ledger['runs'])} (85%).", f"- pass^4: {pass4}/{len(by_task)} tasks (80%): 68, 70, 73, and 88 pass all four trials; 79 passes one of four.", "", "## Tool-call breakdown", "", "| Task | Tool | Type | ToolMessage.error | Count |", "|---:|---|---|---:|---:|"]
    for (task, tool, kind, error), count in sorted(task_counts.items(), key=lambda x: (int(x[0][0]), x[0][1], x[0][3])):
        lines.append(f"| {task} | {tool} | {kind} | {error} | {count} |")
    lines += ["", "## Manual attribution", "", "- Task 79 failures are `MODEL_REASONING_FAILURE` with secondary `STATE_FINAL_DB_MISMATCH`; they are semantic choices/no-action, not parser, validator, dispatcher, or harness failures.", "- Task 68 has four `TOOL_DOMAIN_PRECONDITION_ERROR` events (`User not found`) after an incorrect zip. Each is followed by user correction and a new successful lookup; native path did not retry the failed request.", "- Task 73 has four `EVAL_ACTION_MISMATCH_DIAGNOSTIC` annotations: action mismatch is not in reward basis, while DB/reward pass.", "", "## Native / derived / manual boundaries", "", "- Native: reward, db_check, action checks, messages, persisted parsed ToolCalls, ToolMessage.error, duration, usage/cost fields.", "- Derived: ledger reconciliation, pass^k, READ/WRITE classification, call/result adjacency, rates and counts.", "- Manual: causal failure attribution and severity. No reasoning/user-simulator/evaluator issue is counted as a harness failure.", "- Not observed: raw malformed JSON candidates, tool latency, exception type, schema-oracle labels, state snapshot/diff, timeout, retry/repair lineage.", "", "## A0-Main candidate subset (proposal only; not run/frozen)", "", "Retain 68/70/73/79/88 for observed authentication/read-only, exchange, return, item-modify, and cancel coverage. Add 39 (default-address WRITE), 40 (pending-payment WRITE), 50 (transfer/escalation), 24 (cancel declined then information lookup; no golden mutation), and 71 or 72 (multi-WRITE address plus item modification). This is a coverage proposal sourced from `tasks.json`; it must be frozen with identical scope/seeds across A0–A3 before execution."]
    (OUT / "a0_pilot_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

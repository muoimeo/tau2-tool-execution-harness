"""Validate and summarize frozen A1-Main Retail 58 x 4 artifacts.

This script is post-run only: it reads results/traces and writes derived
summaries under ``analysis/`` without modifying raw benchmark artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


TASK_IDS = (
    "8", "9", "14", "16", "17", "18", "20", "21", "22", "23", "24", "25",
    "28", "35", "37", "38", "39", "40", "44", "46", "48", "50", "52", "53",
    "55", "58", "60", "61", "62", "63", "64", "68", "70", "71", "73", "74",
    "75", "76", "77", "78", "79", "80", "82", "84", "86", "87", "88", "89",
    "91", "93", "95", "96", "101", "103", "104", "107", "110", "113",
)
TRIAL_SEEDS = (626729, 373753, 361454, 1567)
EXPECTED_KEYS = {(task_id, trial, seed) for trial, seed in enumerate(TRIAL_SEEDS) for task_id in TASK_IDS}


def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def key_from_result(row: dict) -> tuple[str, int, int]:
    return str(row["task_id"]), int(row["trial"]), int(row["seed"])


def key_from_lifecycle(value: dict) -> tuple[str, int, int]:
    identity = value["identity"]
    return str(identity["task_id"]), int(identity["trial_index"]), int(identity["trial_seed"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    results_path = root / "results.json"
    traces = root / "traces"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    rows = results["simulations"]
    result_keys = [key_from_result(row) for row in rows]
    unique_result_keys = set(result_keys)

    lifecycles: list[dict] = []
    for path in traces.glob("*.lifecycle.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        value["_path"] = path
        lifecycles.append(value)
    closed = [item for item in lifecycles if item["status"] == "closed"]
    closed_by_key: dict[tuple[str, int, int], list[dict]] = {}
    for item in closed:
        closed_by_key.setdefault(key_from_lifecycle(item), []).append(item)

    failures: list[str] = []
    if len(rows) != 232:
        failures.append(f"serialized episode count is {len(rows)}, expected 232")
    if len(unique_result_keys) != len(rows):
        failures.append("duplicate serialized episode key")
    if unique_result_keys != EXPECTED_KEYS:
        failures.append("serialized episode keys do not equal frozen schedule")
    if any(len(items) != 1 for items in closed_by_key.values()):
        failures.append("multiple closed lifecycles for one logical episode")
    if set(closed_by_key) != unique_result_keys:
        failures.append("lifecycle/result tuple inconsistency")

    trace_integrity_failures = 0
    events: list[dict] = []
    for key, items in closed_by_key.items():
        lifecycle = items[0]
        trace_path = lifecycle["_path"].parent / lifecycle["trace_filename"]
        if not trace_path.exists():
            failures.append(f"missing trace for {key}")
            trace_integrity_failures += 1
            continue
        raw = trace_path.read_bytes()
        lines = raw.splitlines()
        try:
            last_sequence = json.loads(lines[-1])["sequence"]
        except (IndexError, KeyError, json.JSONDecodeError):
            failures.append(f"invalid final trace event for {key}")
            trace_integrity_failures += 1
            continue
        valid = (
            hashlib.sha256(raw).hexdigest() == lifecycle["trace_sha256"]
            and len(lines) == lifecycle["trace_event_count"]
            and last_sequence == lifecycle["trace_last_sequence"]
        )
        if not valid:
            failures.append(f"trace hash/count/sequence mismatch for {key}")
            trace_integrity_failures += 1
            continue
        events.extend(json.loads(line) for line in lines)

    evaluator_mismatches = [
        item for item in lifecycles
        if "Tool call:" in (item.get("operational_failure") or "")
    ]
    if evaluator_mismatches:
        failures.append("evaluator replay mismatch observed in lifecycle artifact")

    if failures:
        raise SystemExit("A1-Main integrity gate failed: " + "; ".join(failures))

    final_events = [event for event in events if event["phase"] == "result_emitted"]
    rewards = [row["reward_info"]["reward"] for row in rows]
    attempts = Counter(item["identity"]["episode_attempt"] for item in closed)
    analysis = root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    dump(analysis / "benchmark_summary.json", {
        "schema_version": "a1-main-benchmark-summary/v1",
        "scheduled_episodes": 232,
        "serialized_episodes": len(rows),
        "unique_serialized_episodes": len(unique_result_keys),
        "benchmark_completion_rate": len(rows) / 232,
        "reward_rate": sum(rewards) / len(rewards),
        "pass1_count": sum(value == 1 for value in rewards),
        "pass1_rate": sum(value == 1 for value in rewards) / len(rewards),
    })
    dump(analysis / "harness_summary.json", {
        "schema_version": "a1-main-harness-summary/v1",
        "tool_call_count": len(final_events),
        "read_call_count": sum(event["native_tool_type"] == "read" for event in final_events),
        "write_call_count": sum(event["native_tool_type"] == "write" for event in final_events),
        "non_read_write_call_count": sum(event["native_tool_type"] not in {"read", "write"} for event in final_events),
        "native_error_count": sum(event["dispatch_status"] == "native_error" for event in final_events),
        "harness_reject_count": sum(event["origin"] == "harness_reject" for event in final_events),
    })
    dump(analysis / "operational_summary.json", {
        "schema_version": "a1-main-operational-summary/v1",
        "operational_attempt_count": len(lifecycles),
        "failed_operational_count": sum(item["status"] == "failed_operational" for item in lifecycles),
        "aborted_attempt_count": sum(item["status"] == "aborted" for item in lifecycles),
        "operational_first_attempt_success_count": attempts[1],
        "operational_first_attempt_success_rate": attempts[1] / 232,
        "episode_attempts_per_completed_episode": dict(sorted(attempts.items())),
    })
    dump(analysis / "integrity_summary.json", {
        "schema_version": "a1-main-integrity-summary/v1",
        "result_without_closed_lifecycle": 0,
        "closed_lifecycle_without_result": 0,
        "duplicate_serialized_episode": 0,
        "trace_integrity_failure_count": trace_integrity_failures,
        "evaluator_mismatch_observed": 0,
        "resume_did_not_reexecute_closed_episode": True,
    })


if __name__ == "__main__":
    main()

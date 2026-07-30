"""Read-only extractor for native tau2 results; it never invokes tau2 or alters input."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    before = sha256(args.results)
    results = json.loads(args.results.read_text(encoding="utf-8"))
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    episodes, calls, seen = [], [], set()
    for simulation in results.get("simulations", []):
        task_id, trial = str(simulation.get("task_id")), simulation.get("trial")
        seen.add((task_id, trial))
        reward_info = simulation.get("reward_info") or {}
        episodes.append({
            "task_id": task_id, "trial": trial, "seed": simulation.get("seed"),
            "duration": simulation.get("duration"),
            "termination_reason": simulation.get("termination_reason"),
            "reward": reward_info.get("reward"), "db_check": reward_info.get("db_check"),
            "action_checks": reward_info.get("action_checks"),
            "communicate_checks": reward_info.get("communicate_checks"),
        })
        for message_index, message in enumerate(simulation.get("messages", [])):
            for call_index, call in enumerate(message.get("tool_calls") or []):
                calls.append({
                    "task_id": task_id, "trial": trial,
                    "logical_call_id": f"{task_id}:{trial}:{message_index}:{call_index}",
                    "attempt_index": 0, "requestor": call.get("requestor"),
                    "tool_name": call.get("name"), "arguments": call.get("arguments"),
                    "tool_call_id": call.get("id"), "source_message_index": message_index,
                })
            if message.get("role") == "tool":
                calls.append({
                    "task_id": task_id, "trial": trial, "tool_result": True,
                    "tool_call_id": message.get("tool_call_id"), "tool_error": message.get("error"),
                    "content": message.get("content"), "source_message_index": message_index,
                })

    planned = {(str(r["task_id"]), r["trial"]) for r in ledger["runs"]}
    summary = {
        "extractor": "a0_passive_telemetry.py", "mode": "post_run_read_only_observer",
        "planned_episodes": len(planned), "serialized_episodes": len(episodes),
        "missing_scheduled_episodes": [
            {"task_id": task_id, "trial": trial}
            for task_id, trial in sorted(planned - seen, key=lambda item: (item[1], item[0]))
        ],
        "tool_call_records": sum("tool_name" in row for row in calls),
        "tool_result_records": sum(row.get("tool_result") is True for row in calls),
        "native_tool_error_results": sum(row.get("tool_error") is True for row in calls),
        "termination_reason_counts": dict(Counter(e["termination_reason"] for e in episodes)),
        "input_sha256_before": before,
    }
    (args.output_dir / "episodes.json").write_text(json.dumps(episodes, indent=2), encoding="utf-8")
    (args.output_dir / "tool-events.json").write_text(json.dumps(calls, indent=2), encoding="utf-8")
    after = sha256(args.results)
    summary["input_sha256_after"] = after
    summary["input_unchanged"] = before == after
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if before != after:
        raise RuntimeError("Input results file changed during read-only extraction")


if __name__ == "__main__":
    main()

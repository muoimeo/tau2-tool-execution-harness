"""Summarize safe provider-retry telemetry without retaining raw requests."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path


SAFE_COLUMNS = [
    "timestamp", "generation_id", "turn_index", "episode_attempt",
    "provider_attempt", "requested_model", "resolved_model", "request_id",
    "completion_id", "transport_request_id", "id_source", "response_type",
    "choices_count", "message_present", "tool_call_count", "request_fingerprint_sha256",
    "task_id", "trial", "timestamp_start", "timestamp_end",
    "http_status", "finish_reason", "latency_ms", "input_tokens",
    "output_tokens", "total_tokens", "message_count", "tool_schema_count",
    "request_size_bucket", "input_token_bucket", "content_present",
    "tool_calls_present", "failure_code", "outcome",
]


def _rate_by(rows: list[dict], dimension: str) -> dict[str, dict[str, int | float]]:
    """Return safe per-dimension totals/empty counts without raw requests."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if dimension == "tools_enabled":
            key = "tools_on" if (row.get("tool_schema_count") or 0) > 0 else "tools_off"
        else:
            key = str(row.get(dimension))
        grouped.setdefault(key, []).append(row)
    return {
        key: {
            "attempts": len(items),
            "empty_attempts": sum(item.get("failure_code") == "PROVIDER_EMPTY_ASSISTANT_MESSAGE" for item in items),
            "empty_rate": sum(item.get("failure_code") == "PROVIDER_EMPTY_ASSISTANT_MESSAGE" for item in items) / len(items),
        }
        for key, items in sorted(grouped.items())
    }


def _completion_id_uniqueness(rows: list[dict]) -> dict[str, int]:
    return _field_uniqueness(rows, "completion_id")


def _field_uniqueness(rows: list[dict], field: str) -> dict[str, int]:
    values = [row.get(field) for row in rows if row.get(field)]
    return {"non_null_count": len(values), "unique_count": len(set(values)), "duplicate_count": len(values) - len(set(values))}


def task_id_from_generation(generation_id: str) -> str:
    return generation_id.split("__", 1)[0].removeprefix("task-")


def _correlation_counts(rows: list[dict]) -> dict[str, int]:
    grouped: dict[str, list] = {}
    for row in rows:
        fingerprint = row.get("request_fingerprint_sha256")
        if fingerprint:
            grouped.setdefault(fingerprint, []).append(row.get("completion_id"))
    repeated = [ids for ids in grouped.values() if len(ids) > 1]
    return {
        "repeated_fingerprint_groups": len(repeated),
        "same_fingerprint_same_completion_id": sum(len(set(ids)) == 1 for ids in repeated),
        "same_fingerprint_different_completion_id": sum(len(set(ids)) > 1 for ids in repeated),
    }


def _recovery(rows: list[dict]) -> dict[str, dict[str, int | float]]:
    result = {}
    for code in ("PROVIDER_EMPTY_ASSISTANT_MESSAGE", "PROVIDER_TIMEOUT", "PROVIDER_HTTP_503"):
        generations = {}
        for row in rows:
            generations.setdefault(row["generation_id"], []).append(row)
        affected = [items for items in generations.values() if any(item.get("failure_code") == code for item in items)]
        recovered = sum(any(item.get("outcome") == "success" for item in items) for items in affected)
        result[code] = {"affected_generations": len(affected), "recovered_generations": recovered, "recovery_rate": recovered / len(affected) if affected else 0.0}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    rows = []
    # Episode sidecars are named ``*.provider-retry.jsonl``. Provider-only
    # controlled probes use ``provider-retry.jsonl`` so they cannot be
    # mistaken for a benchmark episode. Accept both append-only formats.
    for path in sorted(args.trace_root.glob("*provider-retry.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            rows.append({column: raw.get(column) for column in SAFE_COLUMNS})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "provider_attempts.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["task_id", *SAFE_COLUMNS])
        writer.writeheader()
        for row in rows:
            writer.writerow({"task_id": task_id_from_generation(row["generation_id"]), **row})

    empty = [row for row in rows if row["failure_code"] == "PROVIDER_EMPTY_ASSISTANT_MESSAGE"]
    summary = {
        "schema_version": "provider-diagnostic-summary/v2",
        "provider_attempts": len(rows),
        "successful_attempts": sum(row["outcome"] == "success" for row in rows),
        "empty_response_attempts": len(empty),
        "empty_by_task": dict(sorted(Counter(task_id_from_generation(row["generation_id"]) for row in empty).items())),
        "empty_by_request_size_bucket": dict(sorted(Counter(row["request_size_bucket"] for row in empty).items())),
        "empty_by_input_token_bucket": dict(sorted(Counter(row["input_token_bucket"] for row in empty).items())),
        "empty_by_resolved_model": dict(sorted(Counter(row["resolved_model"] for row in empty).items())),
        "empty_rate_by_input_token_bucket": _rate_by(rows, "input_token_bucket"),
        "empty_rate_by_task": _rate_by(rows, "task_id"),
        "empty_rate_by_turn": _rate_by(rows, "turn_index"),
        "empty_rate_by_generation": _rate_by(rows, "generation_id"),
        "empty_rate_by_message_count": _rate_by(rows, "message_count"),
        "empty_rate_by_tools_enabled": _rate_by(rows, "tools_enabled"),
        "finish_reason_distribution": dict(sorted(Counter(row["finish_reason"] for row in rows).items())),
        "completion_id_uniqueness": _completion_id_uniqueness(rows),
        "request_fingerprint_uniqueness": _field_uniqueness(rows, "request_fingerprint_sha256"),
        "fingerprint_completion_id_correlation": _correlation_counts(rows),
        "latency_ms": ({
            "min": min(row["latency_ms"] for row in rows if row["latency_ms"] is not None),
            "median": statistics.median(row["latency_ms"] for row in rows if row["latency_ms"] is not None),
            "max": max(row["latency_ms"] for row in rows if row["latency_ms"] is not None),
        } if any(row["latency_ms"] is not None for row in rows) else {}),
        "provider_failure_recovery": _recovery(rows),
        "model_routing": dict(sorted(Counter(
            f"{row['requested_model']} -> {row['resolved_model']}" for row in rows
        ).items())),
    }
    (args.output_dir / "provider_diagnostic_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

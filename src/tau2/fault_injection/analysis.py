"""Small, deterministic aggregation for FI-v1 scenario observations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tau2.fault_injection.observer import summarize_harness_trace


SUMMARY_FIELDS = (
    "scenario_id", "disposition", "provider_attempt_count", "dispatch_count",
    "write_dispatch_count", "state_hash_before", "state_hash_after",
    "recovery_outcome", "invariant_violations",
)


def summarize(root: str | Path, output: str | Path) -> Path:
    """Write a compact summary without reading or modifying benchmark artifacts."""
    rows = []
    for path in sorted(Path(root).glob("*/*/scenario-observation.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key]) if isinstance(row.get(key), list) else row.get(key) for key in SUMMARY_FIELDS})
    return target


def write_trace_derived_tool_calls(root: str | Path, output: str | Path) -> Path:
    """Regenerate harness tool-call evidence from immutable trace JSONL.

    This is derived analysis only: it never edits scenario observations,
    lifecycle files, traces, results, or benchmark artifacts.
    """
    rows: list[dict] = []
    for trace_root in sorted(Path(root).glob("a[12]-*/*/traces")):
        summary = summarize_harness_trace(trace_root)
        rows.append({
            "track": trace_root.parent.parent.name,
            "scenario_id": trace_root.parent.name,
            "native_tool_calls": summary["native_tool_calls"],
        })
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target

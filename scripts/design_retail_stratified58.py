"""Generate a structural, outcome-blind candidate Retail suite for review."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


SOURCE = Path("data/tau2/domains/retail/tasks.json")
OUT = Path("data/simulations/15_task_suite_design/retail-stratified-58-v1")
WRITE_TOOLS = {
    "cancel_pending_order", "exchange_delivered_order_items",
    "modify_pending_order_address", "modify_pending_order_items",
    "modify_pending_order_payment", "modify_user_address",
    "return_delivered_order_items",
}
QUOTAS = {
    "read_only_or_no_action": 5,
    "cancel_pending_order": 7,
    "modify_pending_order_items": 9,
    "modify_pending_order_address": 7,
    "modify_pending_order_payment": 1,
    "modify_user_address": 2,
    "return_delivered_order_items": 13,
    "exchange_delivered_order_items": 14,
}
RETAINED_CORE = {"24", "39", "40", "50", "68", "70", "71", "73", "79", "88"}
EXCLUDED = {"72"}


def write_count(task):
    return sum(a["name"] in WRITE_TOOLS for a in task["actions"])


def primary_stratum(task):
    writes = [a["name"] for a in task["actions"] if a["name"] in WRITE_TOOLS]
    return writes[0] if writes else "read_only_or_no_action"


def rank(task_id):
    return hashlib.sha256(f"retail-stratified-58-v1|{task_id}".encode()).hexdigest()


def main():
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    tasks = []
    for task in raw:
        actions = (task.get("evaluation_criteria") or {}).get("actions") or []
        task = dict(task)
        task["id"] = str(task["id"])
        task["actions"] = actions
        tasks.append(task)
    by_stratum = defaultdict(list)
    for task in tasks:
        if task["id"] not in EXCLUDED:
            by_stratum[primary_stratum(task)].append(task)
    selected = []
    for stratum, quota in QUOTAS.items():
        fixed = [t for t in by_stratum[stratum] if t["id"] in RETAINED_CORE]
        remainder = sorted((t for t in by_stratum[stratum] if t["id"] not in RETAINED_CORE), key=lambda t: rank(t["id"]))
        chosen = fixed + remainder[: quota - len(fixed)]
        if len(chosen) != quota:
            raise RuntimeError(f"Cannot satisfy quota {stratum}: {len(chosen)}/{quota}")
        selected.extend(chosen)
    selected = sorted(selected, key=lambda t: int(t["id"]))
    if len(selected) != 58 or len({t["id"] for t in selected}) != 58:
        raise RuntimeError("Selection is not 58 unique tasks")
    OUT.mkdir(parents=True, exist_ok=True)

    features = []
    for task in tasks:
        names = [a["name"] for a in task["actions"]]
        args = [a.get("arguments") or {} for a in task["actions"]]
        features.append({
            "task_id": task["id"], "selected_candidate": task["id"] in {t["id"] for t in selected},
            "primary_stratum": primary_stratum(task), "expected_action_count": len(names),
            "num_writes": write_count(task), "compound_multi_write": write_count(task) >= 2,
            "has_transfer": "transfer_to_human_agents" in names,
            "has_payment_argument": any("payment_method_id" in a for a in args),
            "has_item_list": any("item_ids" in a for a in args),
            "has_cancel": "cancel_pending_order" in names,
            "has_exchange": "exchange_delivered_order_items" in names,
            "has_return": "return_delivered_order_items" in names,
            "has_modify_items": "modify_pending_order_items" in names,
            "has_modify_payment": "modify_pending_order_payment" in names,
            "has_modify_order_address": "modify_pending_order_address" in names,
            "has_modify_user_address": "modify_user_address" in names,
            "has_read_or_no_action": write_count(task) == 0,
            "expected_tools": ";".join(names),
        })
    with (OUT / "task-features.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(features[0]))
        writer.writeheader()
        writer.writerows(sorted(features, key=lambda x: int(x["task_id"])))

    selected_ids = [t["id"] for t in selected]
    selected_features = [f for f in features if f["selected_candidate"]]
    manifest = {
        "suite_name": "retail-stratified-58-v1",
        "status": "frozen_approved",
        "frozen_at": "2026-07-25",
        "approval": "User approved the exact 58 IDs for the new paired A0-A3 evaluation.",
        "selection_date": "2026-07-25",
        "source_task_file": str(SOURCE).replace("\\", "/"),
        "num_domain_tasks": len(tasks),
        "num_selected_tasks": len(selected_ids),
        "selection_method": "structural_stratified_coverage_with_deterministic_sha256_tiebreak",
        "outcome_blind": True,
        "selection_inputs": ["evaluation_criteria.actions", "tool names", "action arguments", "write count"],
        "excluded_from_selection_inputs": ["A0 reward", "A0 DB match", "A0 traces", "model failures"],
        "native_execution_order": selected_ids,
        "task_ids": selected_ids,
        "retained_core_ids": sorted(RETAINED_CORE, key=int),
        "explicitly_excluded_ids": {"72": "Duplicates task 71 golden two-WRITE state/tool coverage; differs only in user prompt presentation order."},
        "primary_stratum_quotas": QUOTAS,
        "feature_coverage": {
            "write_count_distribution": dict(sorted(Counter(f["num_writes"] for f in selected_features).items())),
            "tool_task_coverage": {tool: sum(f["has_" + key] for f in selected_features) for tool, key in {
                "cancel_pending_order": "cancel", "exchange_delivered_order_items": "exchange", "return_delivered_order_items": "return",
                "modify_pending_order_items": "modify_items", "modify_pending_order_payment": "modify_payment",
                "modify_pending_order_address": "modify_order_address", "modify_user_address": "modify_user_address",
            }.items()},
            "transfer_tasks": [f["task_id"] for f in selected_features if f["has_transfer"]],
            "read_or_no_action_tasks": [f["task_id"] for f in selected_features if f["has_read_or_no_action"]],
        },
        "frozen": True,
    }
    (OUT / "task-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUT / "task-ids.txt").write_text("\n".join(selected_ids) + "\n", encoding="utf-8")

    report = [
        "# Retail stratified-58 v1 — candidate selection report", "",
        "## Status", "", "Frozen and approved on 2026-07-25 for the new paired A0-A3 evaluation.", "",
        "## Method", "", "- Population: all 114 tasks in `data/tau2/domains/retail/tasks.json`.",
        "- Inputs are structural benchmark fields only: golden action names/arguments and derived write count.",
        "- A0 outcomes/traces were not read by the selection algorithm.",
        "- Primary stratum is the first golden DB-WRITE; no-WRITE tasks form `read_only_or_no_action`.",
        "- Retained core IDs were selected before the 10-task A0 coverage run for structural coverage; remaining candidates are ranked with SHA-256 of `retail-stratified-58-v1|task_id`.",
        "- Task 72 is explicitly excluded because task 71 has identical golden WRITE/state coverage.", "",
        "## Quotas", "", "| Primary stratum | Selected |", "|---|---:|",
    ]
    for stratum, quota in QUOTAS.items(): report.append(f"| {stratum} | {quota} |")
    report += ["", "## Coverage", "", f"- Selected tasks: {', '.join(selected_ids)}.", f"- WRITE-count distribution: {manifest['feature_coverage']['write_count_distribution']}.", f"- Transfer task(s): {', '.join(manifest['feature_coverage']['transfer_tasks'])}.",
        "- `modify_pending_order_payment` appears in only task 40's golden actions across this benchmark, so one is the maximum structurally supported coverage count.",
        "- This clean-suite selection does not guarantee malformed JSON, timeout, transient exception, output-invalid, duplicate-WRITE, or state-diff events. Those remain controlled-fault/harness telemetry measurements.", "",
        "## Protocol notes", "", "- Use this exact frozen suite, native task order, trial seeds, model pair, evaluator, and telemetry schema for A0-A3 paired comparisons.",
        "- Native `tau2 run --seed 300 --num-trials 4` produces trial seeds `626729, 373753, 361454, 1567` in this checkout; do not substitute 300-303 as trial seeds.",
        "- Describe the eventual experiment as a stratified Retail subset, not full Retail benchmark coverage.",
        "- Existing 10-task A0-Main artifacts remain historical coverage-pilot evidence; do not mix their metrics with the proposed 58-task suite."]
    (OUT / "selection-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

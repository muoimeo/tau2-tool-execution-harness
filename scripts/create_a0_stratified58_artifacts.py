"""Create immutable planning artifacts for the approved Retail 58-task A0."""
import hashlib
import json
import random
from pathlib import Path

import yaml


SUITE = Path("data/simulations/15_task_suite_design/retail-stratified-58-v1")
BASE = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1")
ROOT_SEED = 300
TRIALS = 4


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    suite_manifest = SUITE / "task-manifest.json"
    suite = json.loads(suite_manifest.read_text(encoding="utf-8"))
    if not suite.get("frozen") or suite.get("status") != "frozen_approved":
        raise RuntimeError("Suite must be frozen_approved before creating A0 artifacts")
    ids = suite["task_ids"]
    if len(ids) != 58 or len(set(ids)) != 58:
        raise RuntimeError("Expected 58 unique frozen task IDs")
    rng = random.Random(ROOT_SEED)
    trial_seeds = [rng.randint(0, 1_000_000) for _ in range(TRIALS)]
    suite_hash = sha256(suite_manifest)
    for child in ("ledger", "results", "telemetry", "analysis", "preflight/results", "preflight/telemetry"):
        (BASE / child).mkdir(parents=True, exist_ok=True)

    taskset = {
        "task_set_name": "retail-stratified-58-v1",
        "domain": "retail",
        "frozen_at": "2026-07-25",
        "task_suite_manifest": str(suite_manifest).replace("\\", "/"),
        "task_suite_manifest_sha256": suite_hash,
        "source_task_file": "data/tau2/domains/retail/tasks.json",
        "task_ids": ids,
        "native_execution_order": ids,
        "source_benchmark_tasks_remain_unchanged": True,
        "task_order_is_frozen": True,
    }
    (BASE / "taskset.json").write_text(json.dumps(taskset, indent=2), encoding="utf-8")
    manifest = {
        "experiment_id": "EXP-01-A0-Main-retail-stratified-58-v1",
        "phase": "A0_main_native_baseline",
        "classification": "official_stratified_retail_control",
        "included_in_final_baseline_metrics": True,
        "purpose": "New official native A0 control for paired A1-A3 comparison on the approved stratified Retail 58-task suite.",
        "repository": {"git_tag": "v0.1.3", "commit": "5ba9e3e56db57c5e4114bf7f901291f09b2c5619"},
        "benchmark": {"domain": "retail", "source_task_file": "data/tau2/domains/retail/tasks.json", "taskset_file": "taskset.json", "task_suite": "retail-stratified-58-v1", "task_suite_manifest_sha256": suite_hash, "task_ids_in_native_execution_order": ids},
        "execution": {"agent_model": "openai/claude-sonnet-5", "user_model": "openai/claude-opus-4-8", "gateway_api_base": "https://stream-netmind.viettel.vn/aigw/ai/v1", "num_trials": TRIALS, "root_seed": ROOT_SEED, "trial_seeds": trial_seeds, "temperature": 0.0, "max_concurrency": 1, "max_steps": 200, "max_errors": 10, "harness_intervention": False, "passive_telemetry": True},
        "expected_episode_count": len(ids) * TRIALS,
        "comparison_invariants": ["approved_task_suite_and_manifest_hash", "native_execution_order", "trial_seeds", "model_pair_temperature", "max_concurrency_max_steps_max_errors", "benchmark_version_policy_evaluator", "passive_telemetry_schema"],
        "preflight": {"task_id": "71", "num_trials": 1, "root_seed": ROOT_SEED, "scheduled_run_ledger": "preflight/scheduled-runs.json", "output": "preflight/results/task71.json", "telemetry_output": "preflight/telemetry/task71", "included_in_main_metrics": False},
        "artifacts": {"scheduled_run_ledger": "ledger/scheduled-runs.json", "native_results": "results/results.json", "passive_telemetry_directory": "telemetry/a0-main", "analysis_directory": "analysis", "telemetry_extractor": "scripts/a0_passive_telemetry.py"},
        "historical_runs": {"a0_pilot_5task": "retained_as_preflight_evidence_only", "a0_main_10task": "retained_as_historical_coverage_pilot_only", "reuse_in_this_a0": False},
        "raw_artifact_rule": "results_json_is_immutable_after_run",
        "secrets": "not_recorded",
    }
    (BASE / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    runs = []
    for trial, seed in enumerate(trial_seeds):
        for index, task_id in enumerate(ids):
            runs.append({"run_id": f"a0s58-t{task_id}-r{trial}", "task_id": task_id, "task_order_index": index, "trial": trial, "trial_seed": seed, "status": "pending"})
    ledger = {"ledger_version": 1, "experiment_id": manifest["experiment_id"], "root_seed": ROOT_SEED, "task_suite_manifest_sha256": suite_hash, "task_order": ids, "num_trials": TRIALS, "planned_episode_count": len(runs), "runs": runs}
    (BASE / "ledger/scheduled-runs.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    preflight = {"ledger_version": 1, "purpose": "New A0-58 preflight only; excluded from official A0-58 metrics", "root_seed": ROOT_SEED, "planned_episode_count": 1, "runs": [{"run_id": "a0s58-preflight-t71-r0", "task_id": "71", "task_order_index": ids.index("71"), "trial": 0, "trial_seed": trial_seeds[0], "status": "pending"}]}
    (BASE / "preflight/scheduled-runs.json").write_text(json.dumps(preflight, indent=2), encoding="utf-8")
    changelog = """# A0 Retail stratified-58 v1 changelog

## 2026-07-25 — New official A0 creation

- Created a new, independent official A0 experiment over the approved 58-task suite.
- No results are reused from the earlier 5-task A0-Pilot or 10-task A0 coverage run.
- All A1-A3 paired comparisons must reference this suite manifest hash, task order, and trial seeds.
- The task 71 preflight is excluded from official A0-58 metrics.
"""
    (BASE / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


if __name__ == "__main__":
    main()

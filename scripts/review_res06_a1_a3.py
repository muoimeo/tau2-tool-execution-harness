"""Complete an evidence-bounded, manual RES-06 review for A1/A2/A3.

The mappings below are deliberately narrow, and were reviewed against each
failed trajectory's user turns, evaluator criteria, actual tool calls and, for
A1+, trace/lifecycle evidence.  They are *not* an automatic reward classifier.
The script never mutates results, task data, trace, lifecycle or provider files.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
RUNS = {
    "A1": REPO / "data/simulations/30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1",
    "A2": REPO / "data/simulations/40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability",
    "A3": REPO / "data/simulations/50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures",
}
WRITE_TOOLS = {
    "cancel_pending_order", "exchange_delivered_order_items", "modify_pending_order_address",
    "modify_pending_order_items", "modify_pending_order_payment", "modify_user_address",
    "return_delivered_order_items", "transfer_to_human_agents",
}

# Task families checked manually.  A user-simulator label means the visible
# simulator turns changed/withheld the information or goal required by the
# frozen evaluator; it does NOT mean that the agent/harness is proved correct.
USER_SIMULATOR_TASKS = {
    "A1": {"22", "39", "76", "110"},
    "A2": {"8", "22", "39", "71", "110"},
    "A3": {"8", "22", "39", "62", "71", "76", "110"},
}
BENCHMARK_AMBIGUITY_TASKS = {
    "A1": {"38"},
    "A2": {"16", "38"},
    "A3": {"16", "38"},
}
# These two trajectories combine changed user requests and earlier mutations;
# the available artifact cannot honestly assign one primary owner.
INSUFFICIENT = {"A1": {("86", 1)}, "A2": {("86", 1)}, "A3": set()}


def results_file(root: Path) -> Path:
    for candidate in (root / "results.json", root / "results/results.json"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(root)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def classification(stage: str, task: str, trial: int) -> tuple[str, str, str, str]:
    if (task, trial) in INSUFFICIENT[stage]:
        return (
            "INSUFFICIENT_EVIDENCE", "not_attributed", "low",
            "The trajectory includes diverging user requests and completed mutations; available evidence does not support a single causal owner.",
        )
    if task in USER_SIMULATOR_TASKS[stage]:
        return (
            "USER_SIMULATOR_DEVIATION", "user_simulator", "high",
            "Visible simulator turns change, withhold, or contradict information needed for the frozen evaluator target; this is not an execution-harness failure.",
        )
    if task in BENCHMARK_AMBIGUITY_TASKS[stage]:
        return (
            "BENCHMARK_AMBIGUITY", "benchmark", "high",
            "The dialogue permits a policy-valid alternative while the evaluator requires one fixed hidden value/choice; this is not an execution-harness failure.",
        )
    return (
        "MODEL_REASONING_FAILURE", "model", "high",
        "The agent made a structurally valid tool choice but selected the wrong item/order/payment/action, omitted an explicit requested action, or transferred instead of completing an evaluator-required action.",
    )


def tool_events(sim: dict[str, Any]) -> list[dict[str, Any]]:
    return [message for message in sim["messages"] if message.get("role") == "tool" and message.get("error") is True]


def action_evidence(sim: dict[str, Any]) -> str:
    expected = [
        check["action"]["name"] for check in sim["reward_info"].get("action_checks") or []
        if not check.get("action_match")
    ]
    actual = [
        call["name"] for message in sim["messages"] for call in message.get("tool_calls") or []
        if call.get("name") in WRITE_TOOLS
    ]
    return f"unmatched_expected={expected}; actual_write_calls={actual}"


REVIEW_FIELDS = [
    "annotation_id", "run_id", "task_id", "trial_index", "logical_call_id", "trigger",
    "primary_failure_code", "secondary_failure_codes", "stage", "source", "severity",
    "retryability", "recoverability", "expected_action", "actual_action", "final_outcome",
    "evidence_ref", "notes", "attribution_confidence", "review_status", "evidence_type", "review_scope",
]


def review_stage(stage: str, root: Path) -> dict[str, Any]:
    raw = json.loads(results_file(root).read_text(encoding="utf-8"))
    out = root / "analysis"
    out.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    tool_error_contents: Counter[str] = Counter()
    per_task: dict[str, Counter[str]] = defaultdict(Counter)

    for sim in raw["simulations"]:
        task, trial = str(sim["task_id"]), int(sim["trial"])
        reward_zero = sim["reward_info"]["reward"] == 0.0
        if reward_zero:
            code, source, confidence, note = classification(stage, task, trial)
            outcome_counts[code] += 1
            per_task[task][code] += 1
            rows.append({
                "annotation_id": f"{stage.lower()}:{sim['id']}:episode-outcome",
                "run_id": sim["id"], "task_id": task, "trial_index": trial, "logical_call_id": "none",
                "trigger": "evaluator_reward_zero", "primary_failure_code": code,
                "secondary_failure_codes": "STATE_FINAL_DB_MISMATCH" if sim["reward_info"]["reward_breakdown"].get("DB") == 0 else "COMMUNICATION_MISMATCH",
                "stage": "external" if source in {"model", "user_simulator", "benchmark"} else "not_attributed",
                "source": source, "severity": "S2_TASK_BLOCKING", "retryability": "non_retryable",
                "recoverability": "replan_required" if source == "model" else "not_applicable",
                "expected_action": "frozen evaluator criteria", "actual_action": action_evidence(sim),
                "final_outcome": "failed", "evidence_ref": f"results.json simulation_id={sim['id']}; reward_info; full message trajectory",
                "notes": note, "attribution_confidence": confidence, "review_status": "reviewed_v1",
                "evidence_type": "result+task_evaluator+manual_trajectory_review", "review_scope": f"RES-06 {stage} completed-outcome review",
            })

        for index, message in enumerate(tool_events(sim)):
            content = str(message.get("content", ""))
            tool_error_contents[content] += 1
            rows.append({
                "annotation_id": f"{stage.lower()}:{sim['id']}:tool-error:{index}",
                "run_id": sim["id"], "task_id": task, "trial_index": trial,
                "logical_call_id": str(message.get("id", "unknown")), "trigger": "native_tool_message_error",
                "primary_failure_code": "TOOL_DOMAIN_PRECONDITION_ERROR", "secondary_failure_codes": "",
                "stage": "native_tool", "source": "retail_domain_tool", "severity": "S1_RECOVERABLE",
                "retryability": "non_retryable", "recoverability": "replan_or_new_user_input_required",
                "expected_action": "Do not blindly replay the failed native request.",
                "actual_action": f"Native ToolMessage(error=true): {content}",
                "final_outcome": "episode_failed" if reward_zero else "episode_completed",
                "evidence_ref": f"results.json simulation_id={sim['id']}; tool message id={message.get('id')}",
                "notes": "Visible native domain/precondition result. It is not provider failure and must not be counted as a harness-retry candidate solely from ToolMessage.error.",
                "attribution_confidence": "high", "review_status": "reviewed_v1",
                "evidence_type": "native_tool_message", "review_scope": f"RES-06 {stage} tool-error review",
            })

    write_csv(out / "failure_annotations_reviewed_v1.csv", rows, REVIEW_FIELDS)
    operational: dict[str, Any] = {}
    accounting = root / "traces/a2-operational-accounting.json"
    if accounting.exists():
        operational = json.loads(accounting.read_text(encoding="utf-8"))

    payload = {
        "schema_version": "RES-06-cross-stage-reviewed-v1",
        "stage": stage,
        "completed_serialized_episodes": len(raw["simulations"]),
        "completed_reward_zero_episodes": sum(outcome_counts.values()),
        "outcome_primary_failure_counts": dict(sorted(outcome_counts.items())),
        "native_tool_error_event_counts_by_visible_message": dict(sorted(tool_error_contents.items())),
        "native_tool_error_event_count": sum(tool_error_contents.values()),
        "operational_accounting_separate_from_completed_rewards": operational,
        "per_task_completed_reward_zero_counts": {key: dict(value) for key, value in sorted(per_task.items(), key=lambda x: int(x[0]))},
        "boundaries": [
            "Only completed serialized episodes appear in outcome_primary_failure_counts.",
            "A2/A3 operationally unresolved episodes are not reward=0 and remain separate.",
            "TOOL_DOMAIN_PRECONDITION_ERROR records native visible tool errors; it is not a provider/harness failure label.",
            "No causal harness failure is inferred from a benchmark reward failure without trace evidence.",
        ],
    }
    (out / "failure_taxonomy_counts_v1.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        f"# RES-06 completed-outcome review — {stage}", "",
        "## Scope", "",
        f"- Reviewed **{sum(outcome_counts.values())}** completed episodes with `reward=0` out of {len(raw['simulations'])} serialized episodes.",
        f"- Separately reviewed **{sum(tool_error_contents.values())}** native `ToolMessage(error=true)` events.",
        "- This does not convert A2/A3 operationally unresolved episodes into reward=0 benchmark failures.", "",
        "## Primary cause of completed reward-zero episodes", "",
        "| Code | Count | Meaning |", "|---|---:|---|",
    ]
    meanings = {
        "MODEL_REASONING_FAILURE": "Valid execution but wrong/omitted semantic action.",
        "USER_SIMULATOR_DEVIATION": "Visible user simulator behavior diverged from frozen evaluator target.",
        "BENCHMARK_AMBIGUITY": "Dialogue allows a valid alternative but evaluator fixes a different choice.",
        "INSUFFICIENT_EVIDENCE": "Do not assign a causal owner from the available trajectory.",
    }
    for code, count in sorted(outcome_counts.items()):
        lines.append(f"| {code} | {count} | {meanings[code]} |")
    lines += [
        "", "## Native tool-error observations", "",
        "These are precondition/domain responses, not a reason to retry the identical call:", "",
        "| Visible native message | Count |", "|---|---:|",
    ]
    for message, count in sorted(tool_error_contents.items()):
        lines.append(f"| `{message}` | {count} |")
    if operational:
        lines += ["", "## Operationally unresolved (not included above)", "", "```json", json.dumps(operational, indent=2), "```"]
    lines += [
        "", "## Interpretation boundary", "",
        "A1/A2/A3 do not and should not generically prevent model-reasoning, user-simulator, or evaluator-ambiguity failures. Their validated role is structural containment, provider retry/containment, and output/postcondition safety—not access to evaluator ground truth.",
    ]
    (out / "res06_reviewed_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    print(json.dumps({stage: review_stage(stage, root) for stage, root in RUNS.items()}, indent=2))


if __name__ == "__main__":
    main()

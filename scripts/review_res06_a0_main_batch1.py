"""Generate the first, evidence-bounded RES-06 review for A0-Main Retail-58.

The original failure_annotations.csv remains an immutable review queue. This
script writes reviewed derivatives only and deliberately leaves out-of-scope
triggers pending rather than inventing an attribution.
"""
import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1")
ANALYSIS = ROOT / "analysis"


def review(code, secondary, stage, source, severity, retryability, recoverability,
           expected, *tail):
    """Support an optional expected-detail without shifting annotation fields."""
    if len(tail) == 5:
        actual, outcome, evidence, notes, confidence = tail
    elif len(tail) == 6:
        expected_detail, actual, outcome, evidence, notes, confidence = tail
        expected = f"{expected}: {expected_detail}"
    else:
        raise ValueError(f"expected 5 or 6 fields after expected action, got {len(tail)}")
    return {
        "primary_failure_code": code,
        "secondary_failure_codes": secondary,
        "stage": stage,
        "source": source,
        "severity": severity,
        "retryability": retryability,
        "recoverability": recoverability,
        "expected_action": expected,
        "actual_action": actual,
        "final_outcome": outcome,
        "evidence_ref": evidence,
        "notes": notes,
        "attribution_confidence": confidence,
        "review_status": "reviewed_batch1",
        "evidence_type": "native_trace+manual_review",
    }


# Outcome review: selected task/trial pairs where reward=0. The first causal
# event is attributed; final DB mismatch is secondary, never a harness claim.
OUTCOMES = {}
for trial in range(4):
    OUTCOMES[("22", trial)] = review(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Agent changed only the profile address; simulator never asked for the scenario's required order-address work and then ended/transferred.",
        "failed", "turns 1-25", "The frozen scenario requires user and pending-order address changes. The generated user only requests a generic address change; even trial 1 supplies the historic profile address but does not request order changes. This is external simulator constraint drift, not a harness failure.", "medium")
for trial in range(4):
    OUTCOMES[("38", trial)] = review(
        "BENCHMARK_AMBIGUITY", "EVAL_ACTION_MISMATCH_DIAGNOSTIC;STATE_FINAL_DB_MISMATCH", "evaluation", "benchmark",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "escalate",
        "Do not infer a single required cancellation reason when the scenario only asks to cancel after the price branch.",
        "Agent cancelled the correct pending order with policy-allowed reason 'ordered by mistake'; evaluator expects 'no longer needed'.",
        "failed", "cancel_pending_order turns 34/36/38/34 (trials 0/1/2/3)", "The cancellation completed, but all four traces use a different allowed reason selected during dialogue. The scenario does not constrain that reason while evaluator does. This is a benchmark/evaluator specification ambiguity, not a tool or harness fault.", "high")
for trial in range(4):
    OUTCOMES[("91", trial)] = review(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Inspect e-reader availability and exchange it when the same item is available.",
        "Agent returned the e-reader in #W3239882 instead of executing the required exchange.",
        "failed", "return_delivered_order_items turns 22/28/24/26", "All four calls are syntactically valid and dispatched successfully. The required action is exchange_delivered_order_items; this is semantic model reasoning, outside execution harness scope.", "high")
for trial in range(4):
    OUTCOMES[("107", trial)] = review(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Exchange the hiking boots for the frozen same-item replacement and exchange the jigsaw as specified.",
        "Trials 0-1 return boots; trials 2-3 exchange to item 6159919747 instead of evaluator-required same item 1615379700.",
        "failed", "boots call turns 20/24/26/30", "The tool calls execute but select a semantically wrong operation or replacement. A generic schema validator cannot determine the task-intent item.", "high")
OUTCOMES[("18", 1)] = review(
    "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
    "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
    "Permit the requested same-item exchange after the user changes mind.",
    "Agent asserts same-item exchange is impossible; user follows that misinformation and agent returns the chair.",
    "failed", "assistant turn 20; return turn 24", "The frozen action explicitly allows same old/new item id. The misleading model statement precedes the simulator's changed response; do not attribute this primary failure to the simulator.", "high")
for trial in (2, 3):
    OUTCOMES[("18", trial)] = review(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Exchange the chair for the requested same item id 8069050545.",
        "Agent exchanges to variant 4168944673.", "failed",
        "exchange_delivered_order_items turn 26", "Valid tool/schema execution but wrong replacement selection; not validator or harness failure.", "high")
for trial in (0, 1, 3):
    OUTCOMES[("62", trial)] = review(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "After finding no speaker below $100, preserve the scenario branch and provide requested information/final total only.",
        "Simulator accepts a $271.89 replacement although its scenario says to add only a speaker below $100; agent then mutates the pending order.",
        "failed", "user turn 21/25 after no-under-$100 result", "The user changes the task condition after the agent reports no eligible under-$100 item. The WRITE is valid for the later dialogue but outside the frozen scenario; external simulator attribution, not harness failure.", "high")
for trial in (0, 1, 2):
    OUTCOMES[("79", trial)] = review(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Modify #W8661412 item 3453331371 to required item 2439754078.",
        "Agent selects new item 7661609223.", "failed",
        "modify_pending_order_items turns 18/18/16", "The call is parsed and succeeds at execution but the selected item conflicts with frozen task semantics. No generic execution-harness validator can infer this intent.", "high")
for trial in (1, 2, 3):
    OUTCOMES[("103", trial)] = review(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Return only backpack 7824298782 from #W9218746 and preserve frozen same-product item semantics.",
        "Trials 1-3 additionally return vacuum 2872451762; trial 3 also selects 4-piece item 9956648681 rather than required 2-piece red hardshell 8964750292.",
        "failed", "return_delivered_order_items and final modify_pending_order_items calls", "The user asks for a backpack received with a vacuum cleaner, not the vacuum itself; valid calls expand/alter task intent. This is model-side semantic selection, not a dispatch failure.", "medium")
for trial in (0, 1, 2):
    OUTCOMES[("110", trial)] = review(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required", "replan",
        "Use 760 Elm Avenue as the new address and update #W1092119, as frozen evaluator requires.",
        "Simulator explicitly reverses old/new addresses and confirms changing #W1603792/default to 592 Elm Avenue; agent follows that revised request.",
        "failed", "user turns 13/15/15 and subsequent WRITE calls", "Trials 0-2 contradict the frozen scenario/evaluator address mapping. This is a simulator deviation, not an execution-harness failure.", "high")


def tool_review(task, trial, error_text, call_id, turn):
    # Native error strings make domain precondition classification evidence-based.
    if task == "37" and "new item id should be different" in error_text:
        outcome = "recovered"  # the next call is a new re-planned argument set
        return review(
            "TOOL_DOMAIN_PRECONDITION_ERROR", "SCHEMA_CONSTRAINT_VIOLATION", "execution", "model",
            "S1_RECOVERABLE", "non_retryable", "replan_required", "reject",
            "Native tool rejects a modify request containing unchanged old/new item pairs; agent issues a distinct reduced/replanned call.",
            outcome, f"tool_call_id={call_id}; turn={turn}",
            "This is not a transient failure and not a retry of the same request. The explicit message proves the cross-argument invariant; any A1 guard would need a frozen domain constraint, not only signature schema.", "high")
    if task == "82":
        return review(
            "TOOL_DOMAIN_PRECONDITION_ERROR", "", "execution", "model",
            "S1_RECOVERABLE", "non_retryable", "replan_required", "replan",
            "Native tool rejects credit-card refund; dialogue then obtains/uses original gift-card method in a new call.",
            "recovered", f"tool_call_id={call_id}; turn={turn}",
            "The request is valid shape but violates refund business policy. Do not retry the same credit-card request; the later gift-card call is conversational recovery, not a tool retry.", "high")
    if task == "38" and "Order not found" in error_text:
        return review(
            "TOOL_DOMAIN_PRECONDITION_ERROR", "USER_SIMULATOR_DEVIATION", "execution", "user_simulator",
            "S1_RECOVERABLE", "non_retryable", "replan_required", "replan",
            "Reject unknown order id and obtain/lookup the valid order.", "recovered", f"tool_call_id={call_id}; turn={turn}",
            "The random #W0000123 is not a frozen evaluator action; native tool correctly rejects it and a later account lookup obtains #W9348897. Not a transient infrastructure error.", "high")
    # Identity lookup errors in 35/37/38/55/68 are explicit invalid identity data.
    if "User not found" in error_text:
        return review(
            "TOOL_DOMAIN_PRECONDITION_ERROR", "", "execution", "user_simulator",
            "S1_RECOVERABLE", "non_retryable", "replan_required", "replan",
            "Reject invalid identity data and request/correct identity information before a distinct lookup.", "recovered",
            f"tool_call_id={call_id}; turn={turn}",
            "The native message proves a domain lookup miss. The subsequent successful path uses newly supplied/corrected identity information; this is conversational recovery, not retrying the same tool request and not infrastructure evidence.", "high")
    return review(
        "UNKNOWN_EXECUTION_ERROR", "", "execution", "tool", "S1_RECOVERABLE", "unknown", "unknown",
        "escalate", "Native ToolMessage.error persisted without enough classified evidence.", "contained",
        f"tool_call_id={call_id}; turn={turn}", "No stronger attribution is justified.", "low")


def main():
    base_queue = list(csv.DictReader((ANALYSIS / "failure_annotations.csv").open(encoding="utf-8")))
    results = json.loads((ROOT / "results/results.json").read_text(encoding="utf-8"))
    simulations = {(str(s["task_id"]), s["trial"]): s for s in results["simulations"]}
    updates = {}

    for (task, trial), verdict in OUTCOMES.items():
        run_id = next(row["run_id"] for row in base_queue if row["task_id"] == task and int(row["trial_index"]) == trial and row["trigger"] == "native_reward_zero")
        updates[f"{run_id}:episode-outcome"] = verdict

    error_tasks = {"35", "37", "38", "55", "68", "82"}
    for (task, trial), simulation in simulations.items():
        if task not in error_tasks:
            continue
        for turn, message in enumerate(simulation["messages"]):
            if message.get("role") != "tool" or not message.get("error"):
                continue
            updates[f"{message['id']}:tool-error"] = tool_review(task, trial, message.get("content", ""), message["id"], turn)

    columns = [
        "annotation_id", "run_id", "task_id", "trial_index", "logical_call_id", "trigger",
        "primary_failure_code", "secondary_failure_codes", "stage", "source", "severity",
        "retryability", "recoverability", "expected_action", "actual_action", "final_outcome",
        "evidence_ref", "notes", "attribution_confidence", "review_status", "evidence_type", "review_scope",
    ]
    reviewed = []
    for row in base_queue:
        output = {key: "" for key in columns}
        for key in ("annotation_id", "run_id", "task_id", "trial_index", "logical_call_id", "trigger"):
            output[key] = row.get(key, "")
        verdict = updates.get(row["annotation_id"])
        if verdict:
            output.update(verdict)
            output["review_scope"] = "RES-06 batch 1"
        else:
            output.update({
                "primary_failure_code": row.get("primary_failure_code", ""),
                "secondary_failure_codes": row.get("secondary_failure_codes", ""),
                "stage": row.get("stage", "not_attributed"), "source": row.get("source", "not_attributed"),
                "severity": row.get("severity", "not_attributed"),
                "evidence_ref": row.get("evidence_ref", ""), "notes": row.get("notes", ""),
                "attribution_confidence": "not_reviewed", "review_status": "pending_manual_review",
                "evidence_type": "native_result", "review_scope": "outside RES-06 batch 1",
            })
        reviewed.append(output)

    # Whole-trial review of task 110 also found an evaluator diagnostic in a pass.
    s = simulations[("110", 3)]
    extra = {key: "" for key in columns}
    extra.update({
        "annotation_id": "a0s58-t110-r3:evaluator-action-diagnostic", "run_id": "a0s58-t110-r3",
        "task_id": "110", "trial_index": 3, "logical_call_id": "none", "trigger": "action_match_false_reward_pass",
        **review("EVAL_ACTION_MISMATCH_DIAGNOSTIC", "", "evaluation", "evaluator", "S0_INFO", "non_retryable", "unknown", "record diagnostic",
                 "Reward=1 and db_match=true while modify_user_address action check is false.", "success", "reward_info.action_checks",
                 "ACTION is not in reward_basis (DB, COMMUNICATE). This is evaluator diagnostic only, not execution or harness failure.", "high"),
        "review_scope": "RES-06 batch 1",
    })
    reviewed.append(extra)

    with (ANALYSIS / "failure_annotations_reviewed.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(reviewed)

    reviewed_rows = [row for row in reviewed if row["review_status"] == "reviewed_batch1"]
    taxonomy = {
        "scope": "RES-06 batch 1: tasks 22,38,91,107; then 18,62,79,103,110; all tool errors in 35,37,38,55,68,82",
        "total_original_triggers": len(base_queue), "reviewed_rows_including_diagnostic": len(reviewed_rows),
        "pending_original_triggers": sum(row["review_status"] == "pending_manual_review" for row in reviewed),
        "reviewed_primary_failure_counts": dict(Counter(row["primary_failure_code"] for row in reviewed_rows)),
        "reviewed_source_counts": dict(Counter(row["source"] for row in reviewed_rows)),
        "reviewed_final_outcome_counts": dict(Counter(row["final_outcome"] for row in reviewed_rows)),
        "operational_incidents": {
            "source": "resume-attempts.log", "process_exit_code_1": 31,
            "classification": "external operational incidents; excluded from episode/tool failure counts",
            "note": "The log does not persist raw response/body for each exit; do not classify every exit as a tool failure.",
        },
    }
    (ANALYSIS / "failure_taxonomy_counts.json").write_text(json.dumps(taxonomy, indent=2), encoding="utf-8")

    traces = """# RES-06 representative traces — A0-Main batch 1

All references point to the immutable native `results.json`; no raw trace was edited.

## Domain precondition with conversational recovery — task 68

All four trials call `find_user_id_by_name_zip(Noah, Ito, 98178)` and receive `Error: User not found` (tool turn 7). The task instruction explicitly says the simulator first supplies 98178, then corrects to 98187 after an error. The agent requests correction and performs a **new** successful lookup. Classification: `TOOL_DOMAIN_PRECONDITION_ERROR`, S1, non-retryable same request, recovered conversationally. It is not a tool retry or infrastructure failure.

## Cross-argument domain constraint — task 37

All trials initially include unchanged umbrella/backpack pairs in `modify_pending_order_items`; the tool responds `Error: The new item id should be different from the old item id` at turn 33. The agent issues a changed argument set. Classification: `TOOL_DOMAIN_PRECONDITION_ERROR` with `SCHEMA_CONSTRAINT_VIOLATION` secondary, S1. This is a candidate *domain-aware* A1 guard only if the invariant is frozen; it is not evidence for transient retry.

## Semantically valid WRITE with wrong task intent — task 79

Trials 0–2 dispatch `modify_pending_order_items` successfully but replace item 3453331371 with 7661609223; evaluator requires 2439754078. Classification: `MODEL_REASONING_FAILURE` + `SCHEMA_VALID_BUT_SEMANTICALLY_WRONG`; S2. The dispatch path is functioning, so this is outside generic validator/retry scope.

## User-simulator constraint drift — task 110

Trials 0–2 explicitly describe 760 Elm Avenue as old and 592 Elm Avenue as new, opposite to frozen evaluator action. The agent follows the simulated request. Classification: `USER_SIMULATOR_DEVIATION`; S2 external attribution. Trial 3 instead matches DB/reward but has `action_match=false`, recorded separately as `EVAL_ACTION_MISMATCH_DIAGNOSTIC` S0.

## Benchmark/evaluator ambiguity — task 38

Each trial completes cancellation of the correct pending order after the user chooses `ordered by mistake`; evaluator requires `no longer needed`, although scenario does not fix a reason. Classification: `BENCHMARK_AMBIGUITY`, not tool/harness failure.
"""
    (ANALYSIS / "representative_traces.md").write_text(traces, encoding="utf-8")

    report = f"""# RES-06 interim analysis — A0-Main batch 1

## Scope and integrity

- Reviewed {len(reviewed_rows)} annotations: {len(base_queue)} original A0 triggers plus one evaluator diagnostic found during whole-trial review.
- {taxonomy['pending_original_triggers']} original triggers remain pending; no causal code was invented for them.
- Native raw results, telemetry, benchmark source, task data, and original annotation queue were not modified.
- `resume-attempts.log` records 31 process exits with code 1. They are operational incidents, not episode/tool failure rows.

## Observed attribution

```json
{json.dumps(taxonomy['reviewed_primary_failure_counts'], indent=2)}
```

Most reviewed successful tool errors are domain preconditions followed by a new conversational turn. The evidence does **not** support transient/permanent labels, timeout claims, or automatic retry of the same request.

## A1 validator / containment proposal

- Block/return structured error for malformed parse, unknown registry, and signature-schema faults when observed in A1; none is native-observed in this batch.
- Consider a domain-aware pre-dispatch reject for `modify_pending_order_items` where an old item id equals its proposed new item id. This must be an explicit frozen cross-argument constraint; generic signature validation alone cannot establish it.
- Do not make A1 decide task-intent item ids, order/address semantics, cancellation reason, or refund policy from a generic validator. Those failures here are model, simulator, or benchmark/evaluator attributions.

## A2 retry proposal

- No reviewed native event is evidenced as transient. Do **not** retry `User not found`, `Order not found`, unchanged-item, or wrong-payment requests.
- A2 retry should be reserved for controlled-fault/pre-labelled transient exception or timeout cases, with WRITE idempotency protection.
- The 31 process exits require separate LLM response-contract telemetry before any retry policy can be attributed to gateway/model rather than tool execution.

## A3 repair/recovery proposal

- Conversational recovery is observed after user correction (identity/order/payment); keep it as replan, not hidden tool retry.
- Deterministic repair may be evaluated for unchanged old/new item pairs only after freezing an oracle/constraint and auditing WRITE safety. Do not silently repair model intent or payment choice.

## Outside generic harness scope

- `MODEL_REASONING_FAILURE`, `USER_SIMULATOR_DEVIATION`, `BENCHMARK_AMBIGUITY`, and `EVAL_ACTION_MISMATCH_DIAGNOSTIC` are attribution/evaluation evidence, not core harness failures.
- Semantic wrong IDs and task-specific business intent require model-side replan, task oracle, or protocol changes—not a generic dispatcher retry.
"""
    (ANALYSIS / "res06_interim_analysis.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()

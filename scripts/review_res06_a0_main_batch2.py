"""Complete the evidence-bounded RES-06 review for A0-Main Retail-58.

This script reads the batch-1 derivative and writes a new final derivative.
It never edits raw results, the original trigger queue, benchmark tasks, or
telemetry.  ``INSUFFICIENT_EVIDENCE`` is a review disposition requested for
mixed-cause traces; it is deliberately not promoted to a canonical RES-06
execution failure class.
"""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1")
ANALYSIS = ROOT / "analysis"
RAW_RESULTS = ROOT / "results/results.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verdict(code, secondary, stage, source, severity, retryability, recoverability,
            expected, actual, outcome, evidence, notes, confidence):
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
        "review_status": "reviewed_batch2",
        "evidence_type": "native_trace+task_evaluator+manual_review",
        "review_scope": "RES-06 batch 2",
    }


UPDATES = {
    "a0s58-t16-r0:episode-outcome": verdict(
        "BENCHMARK_AMBIGUITY", "EVAL_ACTION_MISMATCH_DIAGNOSTIC;STATE_FINAL_DB_MISMATCH",
        "evaluation", "benchmark", "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Return the watch using evaluator-fixed paypal_5364164.",
        "After the agent asks, simulator chooses valid gift_card_1675628; the agent confirms and dispatches it.",
        "failed", "task 16 scenario; user turns 19/23; return call turn 28",
        "The scenario requires a watch return and total only; it does not prescribe a refund method. The evaluator requires PayPal while the dialogue supplies a valid gift card. This cannot support a harness attribution.", "high"),
    "a0s58-t20-r0:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH",
        "external", "model", "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Upgrade all four pending-order items to evaluator-required most-expensive variants, including shoe 4107812777.",
        "Agent presents and dispatches running-shoe item 4153505238 despite user request for most expensive variants.",
        "failed", "task 20 scenario/evaluator; assistant turn 24; WRITE turn 26",
        "The call is structurally valid and succeeds. Selection is semantically wrong relative to an explicit user criterion; generic validation cannot infer the item ranking.", "high"),
    "a0s58-t20-r1:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH",
        "external", "model", "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Upgrade all four eligible items as specified by task 20.",
        "Agent asserts Water Bottle and Mechanical Keyboard cannot be modified; user then confirms only two upgrades and agent writes only two.",
        "failed", "task 20 evaluator; assistant turns 25-28; WRITE turn 30",
        "The frozen evaluator explicitly expects all four replacements. The partial scope follows the agent's unsupported earlier statement, so the primary causal error is model reasoning.", "high"),
    "chatcmpl-tool-a310f3a3aa43875d:tool-error": verdict(
        "TOOL_DOMAIN_PRECONDITION_ERROR", "USER_SIMULATOR_DEVIATION", "execution", "user_simulator",
        "S1_RECOVERABLE", "non_retryable", "replan_required",
        "Reject unknown order and obtain corrected order id before a new lookup.",
        "Simulator supplies 9502126; get_order_details returns 'Order not found'; simulator corrects to 9502127 and the new lookup succeeds.",
        "recovered", "task 46 user turn 9; ToolMessage.error turn 11; user turn 15; successful call turn 16",
        "This is an explicit domain lookup miss caused by supplied data, followed by conversational recovery. It is not a transient service failure and the later distinct call is not a retry.", "high"),
    "a0s58-t61-r0:episode-outcome": verdict(
        "BENCHMARK_AMBIGUITY", "EVAL_ACTION_MISMATCH_DIAGNOSTIC;STATE_FINAL_DB_MISMATCH",
        "evaluation", "benchmark", "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Modify earbuds with evaluator-fixed paypal_3742148.",
        "Simulator explicitly asks for valid gift_card_3406421, which the agent dispatches successfully.",
        "failed", "task 61 scenario/evaluator; user turn 11; WRITE turn 12",
        "The scenario constrains colour and price but not refund method; the evaluator fixes PayPal. This is evaluator/task ambiguity, not a tool or harness failure.", "high"),
    "a0s58-t71-r0:episode-outcome": verdict(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Update #W5270061 to default Charlotte address, then only modify backpack with PayPal.",
        "Simulator first confirms address update, then explicitly retracts it and asks for backpack only; agent follows the revised request.",
        "failed", "task 71 scenario/evaluator; user turns 21/23/25; WRITE turn 26",
        "The frozen scenario's confirmation branch changes payment/item scope, not the address action. The simulator retracts an evaluator-required mutation; this is external simulator drift.", "high"),
    "a0s58-t93-r0:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH",
        "external", "model", "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Identify and exchange the 15-inch, 32GB laptop in #W4073673.",
        "Agent inspects/exchanges laptop 3478699712 in #W2905754 instead.",
        "failed", "task 93 scenario/evaluator; order calls turns 10/12; exchange turn 20",
        "A valid exchange is dispatched against the wrong order/item despite the scenario's identifying properties. This is semantic selection, not execution failure.", "high"),
    "chatcmpl-tool-85c3259011c8743d:tool-error": verdict(
        "TOOL_DOMAIN_PRECONDITION_ERROR", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG", "execution", "model",
        "S1_RECOVERABLE", "non_retryable", "replan_required",
        "Use the delivered camera item id 5996159312.",
        "Agent sends 5996159314; native tool responds 'Number ... not found'; agent then uses 5996159312 in a new successful call.",
        "recovered", "task 52 evaluator; ToolMessage.error turn 15; calls turns 14/16",
        "The error string proves an item-domain precondition miss. The altered identifier is a model semantic/copy error; the follow-up is re-planning, not same-request retry.", "high"),
    "chatcmpl-tool-a11786dbe39494f8:tool-error": verdict(
        "TOOL_DOMAIN_PRECONDITION_ERROR", "USER_SIMULATOR_DEVIATION", "execution", "user_simulator",
        "S1_RECOVERABLE", "non_retryable", "replan_required",
        "Reject fabricated/unknown order and obtain the actual delivered order.",
        "Simulator gives #W102938; tool returns 'Order not found'; it later corrects to #W5838674 and the agent completes exchange.",
        "recovered", "task 58 user turns 1/11; ToolMessage.error turn 7; successful call turn 12",
        "The native domain check behaves correctly. The scenario does not supply #W102938; the simulator introduces it, so no infrastructure/transient inference is warranted.", "high"),
    "chatcmpl-tool-937e8429c72599a2:tool-error": verdict(
        "TOOL_DOMAIN_PRECONDITION_ERROR", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG", "execution", "model",
        "S1_RECOVERABLE", "non_retryable", "replan_required",
        "Use paypal_3742148 as supplied by user, or re-read profile before a new WRITE.",
        "Agent sends nonexistent paypal_3742149; native tool rejects it; agent reads user details and dispatches paypal_3742148.",
        "recovered", "task 61 user turn 9; ToolMessage.error turn 13; calls turns 12/14/16",
        "Explicit 'Payment method not found' proves a domain precondition. It is a model identifier error and is recovered by a new, corrected request—not an automatic retry.", "high"),
    "a0s58-t86-r1:episode-outcome": verdict(
        "INSUFFICIENT_EVIDENCE", "USER_SIMULATOR_DEVIATION;MODEL_REASONING_FAILURE;STATE_FINAL_DB_MISMATCH",
        "external", "mixed_or_unknown", "S2_TASK_BLOCKING", "non_retryable", "unknown",
        "Modify pending Fleece Jacket #W2466703 and update the default address.",
        "Address is updated, but no jacket WRITE occurs after a sequence involving an incorrect initial order and both agent/user statements to wait for delivery.",
        "failed", "task 86 scenario/evaluator; turns 9-33; only address WRITE turn 26",
        "Trace shows mixed divergence: initial wrong order, an agent statement to wait, then simulator acceptance of waiting despite later identifying a pending jacket. It does not establish one causal owner. This is a review disposition, not a canonical harness failure class.", "low"),
    "a0s58-t37-r2:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "After rejection, modify only required cheapest replacements while preserving items that already are cheapest.",
        "Agent's second call changes additional valid old/new pairs to different, more-expensive items merely to avoid unchanged-id rejection.",
        "failed", "task 37 evaluator; rejected call turn 32; succeeding but wrong call turn 36",
        "The native error is correctly contained; the subsequent successful WRITE violates task economics/intent. This is model semantic reasoning, not a case for blind retry.", "high"),
    "a0s58-t60-r2:episode-outcome": verdict(
        "BENCHMARK_AMBIGUITY", "EVAL_ACTION_MISMATCH_DIAGNOSTIC;STATE_FINAL_DB_MISMATCH", "evaluation", "benchmark",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Modify earbuds using evaluator-fixed paypal_3742148.",
        "Simulator selects valid gift_card_3406421 after the agent asks for refund method; WRITE otherwise matches item selection.",
        "failed", "task 60 scenario/evaluator; user turn 11; WRITE turn 12",
        "Task 60 does not prescribe a payment method but evaluator does. A valid dialogue choice becomes a DB mismatch; do not call that execution failure.", "high"),
    "a0s58-t61-r2:episode-outcome": verdict(
        "BENCHMARK_AMBIGUITY", "EVAL_ACTION_MISMATCH_DIAGNOSTIC;STATE_FINAL_DB_MISMATCH", "evaluation", "benchmark",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Modify earbuds using evaluator-fixed paypal_3742148.",
        "Simulator selects valid gift_card_3406421; agent correctly uses requested method and required item.",
        "failed", "task 61 scenario/evaluator; user turn 11; WRITE turn 12",
        "Same payment-method ambiguity as task 60, independently observed. It is not a retry/validator/harness defect.", "high"),
    "chatcmpl-tool-82fef481814d34ac:tool-error": verdict(
        "TOOL_DOMAIN_PRECONDITION_ERROR", "USER_SIMULATOR_DEVIATION", "execution", "user_simulator",
        "S1_RECOVERABLE", "non_retryable", "replan_required",
        "Reject unknown #W1234567 and obtain correct order context before a new action.",
        "Simulator supplies #W1234567; tool returns 'Order not found'; later dialogue supplies #W2466703 and a distinct flow succeeds.",
        "recovered", "task 86 user turns 7/13; ToolMessage.error turn 9",
        "This is an explicit domain lookup miss caused by user-provided data. It cannot evidence transient infrastructure behavior or auto-retry eligibility.", "high"),
    "a0s58-t93-r2:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Identify and exchange the 15-inch, 32GB laptop in #W4073673.",
        "Agent exchanges #W2905754 item 3478699712 after inspecting only two orders.",
        "failed", "task 93 scenario/evaluator; order calls turns 8/10; exchange turn 18",
        "The task's disambiguating properties are sufficient but not used to locate the expected order. Valid dispatch, wrong semantic choice.", "high"),
    "a0s58-t21-r3:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Modify pending-order items through modify_pending_order_items when confirmed.",
        "Agent tells user exchange must wait until delivery and makes no WRITE, although frozen task expects pending modification.",
        "failed", "task 21 evaluator; assistant turn 20; trace ends turn 29 without WRITE",
        "This is a policy/operation-selection reasoning error. There is no dispatcher or tool failure to intercept after the model declines action.", "high"),
    "a0s58-t28-r3:episode-outcome": verdict(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Do not cancel whole #W2575533 when individual hose cancellation is unsupported; complete only specified returns and total.",
        "Simulator explicitly asks to cancel whole #W2575533 with 'no longer needed'; agent follows it.",
        "failed", "task 28 scenario/evaluator; user turn 17; cancel WRITE turn 20",
        "Frozen scenario says to forget the pending-order request if one item cannot be cancelled. Simulator replaces that condition with full cancellation, so this is not an execution-harness failure.", "high"),
    "a0s58-t76-r3:episode-outcome": verdict(
        "USER_SIMULATOR_DEVIATION", "STATE_FINAL_DB_MISMATCH", "external", "user_simulator",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Cancel skateboard order #W1242543 with reason 'no longer needed' if modification unavailable.",
        "Simulator explicitly instructs 'ordered by mistake' for both cancellations; agent follows it.",
        "failed", "task 76 scenario/evaluator; user turn 25; cancel WRITE turn 28",
        "The scenario fixes a different reason for the skateboard fallback. Dialogue contradicts it; native call itself is valid and successful.", "high"),
    "a0s58-t104-r3:episode-outcome": verdict(
        "MODEL_REASONING_FAILURE", "SCHEMA_VALID_BUT_SEMANTICALLY_WRONG;STATE_FINAL_DB_MISMATCH", "external", "model",
        "S2_TASK_BLOCKING", "non_retryable", "replan_required",
        "Return required bookshelf/jigsaw scope by order and return only backpack from #W9218746.",
        "Agent omits jigsaw from #W8660475, returns extra vacuum 2872451762, and completes other valid mutations.",
        "failed", "task 104 scenario/evaluator; order reads turns 10-18; return WRITEs turns 22/26",
        "The user request and inventory are available in trace, but agent selects wrong multi-order/item scope. Calls dispatch normally; this is semantic reasoning.", "high"),
}


def main():
    before = sha256(RAW_RESULTS)
    source = ANALYSIS / "failure_annotations_reviewed.csv"
    target = ANALYSIS / "failure_annotations_reviewed_v1.csv"
    rows = list(csv.DictReader(source.open(encoding="utf-8")))
    columns = list(rows[0])
    changed = 0
    for row in rows:
        update = UPDATES.get(row["annotation_id"])
        if update:
            row.update(update)
            changed += 1
    if changed != len(UPDATES):
        raise RuntimeError(f"updated {changed}, expected {len(UPDATES)}")
    pending = [r["annotation_id"] for r in rows if r["review_status"] == "pending_manual_review"]
    if pending:
        raise RuntimeError(f"review still pending: {pending}")
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)
    after = sha256(RAW_RESULTS)
    if before != after:
        raise RuntimeError("raw results changed; refusing to continue")

    review_rows = [r for r in rows if r["review_status"].startswith("reviewed_")]
    primary = Counter(r["primary_failure_code"] for r in review_rows)
    secondary = Counter()
    for r in review_rows:
        for code in filter(None, r["secondary_failure_codes"].split(";")):
            secondary[code] += 1
    payload = {
        "scope": "A0-Main Retail-58: all 83 original triggers plus one action_match diagnostic discovered in batch 1",
        "raw_results_sha256_verified_before_after": before,
        "original_trigger_rows": 83,
        "added_evaluator_diagnostic_rows": 1,
        "total_reviewed_rows": len(review_rows),
        "pending_rows": 0,
        "primary_failure_counts": dict(sorted(primary.items())),
        "secondary_context_counts": dict(sorted(secondary.items())),
        "source_counts": dict(sorted(Counter(r["source"] for r in review_rows).items())),
        "outcome_counts": dict(sorted(Counter(r["final_outcome"] for r in review_rows).items())),
        "disposition_note": "INSUFFICIENT_EVIDENCE is a manual-review disposition, not a canonical RES-06 execution code and must not be counted as harness failure.",
        "operational_incidents_excluded": {"resume-attempts.log_process_exit_code_1": 31},
    }
    (ANALYSIS / "failure_taxonomy_counts_v1.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

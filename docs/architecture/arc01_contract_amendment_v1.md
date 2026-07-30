# ARC-01A — A1 execution-contract amendment v1

## Status and evidence scope

This amendment freezes A1 contracts before HAR-01 runtime implementation. It
does not modify tau2 native behavior.

Native error-budget evidence is limited to code reachable in tag `v0.1.3`,
commit `5ba9e3e56db57c5e4114bf7f901291f09b2c5619`:

- `Orchestrator.__init__` initializes `self.num_errors = 0` at
  `src/tau2/orchestrator/orchestrator.py:72`.
- `Orchestrator.run` compares it with `max_errors` at `:259`.
- `_count_errors` exists at `:434-440` but has no call site.
- Repository-wide search found no assignment/increment to `num_errors` after
  initialization.
- `tests/test_arc01_native_error_budget.py` is a deterministic, no-gateway
  regression test: two error `ToolMessage`s are emitted while `num_errors`
  remains zero and termination is not `too_many_errors`.

Conclusion: native `max_errors` is a frozen configuration field, but no active
native error-budget behavior is evidenced in this checkout. This statement does
not infer author intent or rule out code outside this repository.

## Frozen A1 decisions

| Area | Decision |
|---|---|
| Native error semantics | A1 must not call `_count_errors`, assign/increment `Orchestrator.num_errors`, or otherwise make `max_errors` effective. |
| Harness rejection count | Increment `harness_rejection_count` only in sidecar telemetry. It must not affect native termination reason, reward, state, `max_steps`, or `max_errors`. |
| Parser input | Parser receives expected requestor from its caller. Agent response path supplies `assistant`; user-tool response path supplies `user`. A parsed/rejected proposed call always has a non-null requestor. |
| Parser rejection before native ToolCall | Emit a `ToolCallRequest` with `source_kind="synthetic_parser_rejection"`; it records raw extracted fields without claiming the provider emitted a valid native ToolCall. The parser adapter creates a synthetic envelope only to keep the existing message loop tool-compatible. |
| Synthetic call ID | Superseded by `arc01a_contract_clarifications_v2.md` §1: v2 adds `malformed_call_ordinal` to a NUL-delimited UTF-8 SHA-256 input so identical malformed calls in one turn cannot collide. No random UUID or timestamp participates. |
| Synthetic envelope | `ToolCall(id=synthetic_id, name="__harness_parse_error__", arguments={"error_code": code}, requestor=requestor)` is internal harness protocol, never registry-resolved or natively dispatched. It is paired with the canonical rejection `ToolMessage`. |
| Schema mode | Validate `Tool.params` with strict mode and `extra="forbid"`. Required fields must be present. No coercion, default injection, dropping of extra fields, or argument rewrite is allowed. |
| Requestor/registry | Validate non-null requestor and authorization before schema/domain validation. A non-empty tool name must exist in the selected requestor toolkit. |
| Domain constraints | A1 v1 admits only `retail.modify_pending_order_items.old_new_distinct.v1`; it rejects aligned equal old/new item IDs. It does not evaluate item ranking, order choice, payment preference, policy intent, evaluator criteria or ground truth. |
| Canonical reject result | `ToolMessage(id=call_id, role="tool", requestor=requestor, error=true, content="Error: A1 validation rejected tool call [<CODE>].")`. Detailed cause is sidecar `HarnessError`, not added to model-visible message. |
| Dispatch invariant | Reject means `dispatch_count=0`, `dispatch_attempted=false`, no call to native `Environment.get_response`, and no DB mutation. Allow means exactly one native dispatcher call. |
| Raw retention/redaction | Normal A1 sidecar telemetry stores request/result hashes, field names/types, codes and redacted summaries only. It must not duplicate full provider raw response, API headers, API keys, authorization/cookies or unredacted raw argument text. Safe deterministic fixtures may retain literal raw strings. |
| State hash scope | In deterministic local WRITE tests only, hash `environment.tools.get_db_hash()` immediately before and after the executor decision. Rejected WRITEs require equality. Live A1 does not claim full snapshot, postcondition or rollback coverage. |
| Deferred capabilities | Output validation, exception retry, timeout retry, repair, recovery, rollback/idempotency, state postconditions and active error budget are outside A1. They require later architecture and controlled-fault evidence. |

## Compatibility constraints

- Native `results.json` schema remains unchanged.
- Harness details are append-only sidecar telemetry.
- A valid A1 `ALLOW` passes exactly the original parsed name/arguments to the
  native dispatcher once.
- A parser rejection is an A1 containment event; it is not counted as a native
  tool execution error or evidence of a transient gateway/tool failure.
- A model may react conversationally to a canonical rejection in a later turn;
  that is not harness repair or execution retry.

## Explicit unresolved decisions

1. The exact safe redaction transform for values such as addresses and emails
   needs implementation-level tests before any live A1 telemetry capture.
2. Parser treatment of a response containing nonempty text plus one malformed
   call is specified to preserve text and emit one synthetic envelope per bad
   call, but multi-call ordering needs fixture coverage before integration.
3. The A1 run manifest/output naming convention and implementation commit are
   deferred to HAR-07/EXP-02 after the executor exists.

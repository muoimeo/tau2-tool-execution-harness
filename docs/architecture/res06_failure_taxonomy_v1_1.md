# RES-06 v1.1 — Failure taxonomy and retry eligibility freeze

Status: frozen for REC-02 design.  This document separates a failure's source
from the action an A2 execution controller may take.  It does not alter the
native evaluator, Retail tools, task data, or A0/A1 raw artifacts.

## Evidence boundary

* A0 review: 37 observed `ToolMessage.error` events are native domain or
  precondition responses; none is evidence of timeout, transient tool failure,
  parser rejection, registry rejection, schema rejection, or execution retry.
* A1-Main: 43 native domain/precondition errors, 3 pre-dispatch harness rejects,
  51 logged empty `AssistantMessage` interruptions, 61 aborted attempts, and
  10 aborted attempts without retained causal evidence.
* `INSUFFICIENT_EVIDENCE` is a review disposition, **not** a runtime failure
  code and never a retry trigger.

## Frozen classification and A2 action

| Class | Source / required evidence | A2 retry eligibility | Required action |
|---|---|---|---|
| `TOOL_DOMAIN_PRECONDITION_ERROR` | Public native tool response identifies a domain/precondition miss, e.g. user/order/payment not found or unchanged replacement item | Never | Emit the native response. The next, changed user/agent request may proceed; it is conversational recovery, not retry. |
| `HARNESS_REJECT` | Parser/registry/requestor/schema/domain validator rejects before dispatch | Never | Emit canonical rejection/native-equivalent text. Require `dispatch_count=0`. |
| `PARSER_REJECT`, `REGISTRY_REJECT`, `SCHEMA_REJECT` | Corresponding A1 structured validation result | Never | Same as harness reject. Do not regenerate, repair JSON, or retry. |
| `PROVIDER_EMPTY_ASSISTANT_MESSAGE` | A typed adapter/provider outcome, or exact normalized empty-message contract before `AssistantMessage.validate()` | Eligible | Retry the **model generation boundary** only, within REC-02 budget. No native tool was dispatched by this failure. |
| `PROVIDER_TIMEOUT` | Typed timeout from the provider/transport boundary before a validated assistant response | Eligible | Retry model generation only, within budget. |
| `PROVIDER_HTTP_503` | Typed provider/transport status 503 before a validated assistant response | Eligible | Retry model generation only, within budget. |
| `PROVIDER_INTERRUPTION` | Typed, normalized transport/provider interruption before a validated assistant response | Eligible only if explicitly classified transient | Retry model generation only, within budget. Unknown transport exceptions are not eligible. |
| `NATIVE_EXECUTION_EXCEPTION` | Exception crosses the native callable boundary | Not eligible in A2 v1 | Contain/record once. No blanket dispatcher retry. Future policy requires controlled typed evidence and WRITE safety proof. |
| `UNKNOWN_EXECUTION_ERROR` | Fallback without a typed classification | Never | Stop/contain and investigate; no retry by message matching. |
| `INSUFFICIENT_EVIDENCE` | Missing or conflicting evidence, including 10 A1 aborted attempts | Never | Preserve artifacts and report operationally; do not infer provider failure. |
| Model reasoning, simulator deviation, benchmark ambiguity, evaluator diagnostic | Trace/review evidence outside execution transport | Never | Keep outside generic execution retry/harness-success denominators. |

## Non-negotiable safety invariants

1. A2 retry is a controller around provider generation, not a generic retry in
   `NativeToolExecutor`/dispatcher.
2. A rejected call is never retried and has zero native dispatches.
3. Native domain/precondition results are returned once and never retried.
4. A WRITE may not be repeated after its callable boundary begins.  A2 v1 only
   retries failures proven to occur before any `ToolCallRequest` is dispatched.
5. Every retry decision records attempt index, classification, and budget state;
   unknown evidence fails closed.

## Controlled-fault coverage required before A2 gateway preflight

The repository must deterministically demonstrate: one empty response then
success; timeout/503 then success; exhausted budget; no retry for native domain
error; no retry for harness rejection; and a WRITE invocation count of exactly
one.  These tests establish policy behavior, not live provider reliability.

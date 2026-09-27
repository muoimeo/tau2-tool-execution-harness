# Retail native-error compatibility matrix (A1 v1)

## Scope and decision

The native evaluator replays a serialized trajectory through
`Environment.set_state()` and compares each generated `ToolMessage` to the
serialized one exactly.  Therefore, an A1 call that is **allowed** to reach a
native Retail callable must preserve the native visible error content whenever
that content is approved for retention.  This document does not change Retail
tools, evaluator logic, reward, task data, or ground truth.

Classification is by execution path and approved content policy, not by the
Python exception class alone.

| Class | Native / harness source | Dispatch | Visible `ToolMessage` policy | Evaluator replay claim |
| --- | --- | ---: | --- | --- |
| `NATIVE_DOMAIN_PRECONDITION` | Bound Retail callable raises a known, safe domain error (for example `User not found`) | 1 | Exact native `Error: <message>` when message is bounded and passes redaction | Exact parity required |
| `NATIVE_TOOL_LOOKUP_ERROR` | Bound Retail lookup callable cannot resolve user/order/product/payment | 1 | Same controlled native policy | Exact parity required |
| `NATIVE_TOOL_VALIDATION_ERROR` | Native callable rejects an argument after dispatch | 1 | Same policy only if explicitly approved and safe; otherwise safe fallback | Parity only for approved rows |
| `HARNESS_DOMAIN_REJECT_NATIVE_EQUIVALENT` | Frozen `modify_pending_order_items` same-item rule | 0 | `Error: The new item id should be different from the old item id` | Replay-compatible visible content only; **not native-dispatch parity** |
| `HARNESS_REJECT_STRUCTURAL` | Parser, registry, requestor, or strict schema | 0 | Stable `Tool call rejected: <CODE>.` | No native equivalent claimed; separate harness-defined outcome |
| `TRANSIENT_INFRA_ERROR` | Provider/gateway/trace I/O before a native tool result exists | 0 or no call | No invented native ToolMessage; fail-fast operational lifecycle | Not an evaluator replay case |
| `UNEXPECTED_NATIVE_ERROR` | Unapproved native exception or unsafe message | 1 | `Tool execution failed.` plus scrubbed sidecar error | Not evaluator-compatible; fail closed and retain operational evidence |

Every harness reject has `dispatch_count=0`, `native invocation=0`, and trace
`origin=harness_reject`. The one domain row above is deliberately
native-*equivalent* at the visible-message boundary; it must not be relabelled
as native-dispatch parity.

## Retail native error paths

The following paths are static code evidence from
`src/tau2/domains/retail/tools.py`.  They are grouped by public tool family.

| Tool family | Principal native error paths | Matrix class | A0 evidence |
| --- | --- | --- | --- |
| `find_user_id_by_name_zip`, `find_user_id_by_email`, `get_user_details` | `User not found` | lookup | tasks 35, 37, 38, 55, 68 |
| `get_order_details` | `Order not found` | lookup | tasks 38, 46, 58, 86 |
| `get_product_details` | `Product not found`; variant helper may raise `Variant not found` | lookup | static path; no selected A0 error row required |
| `calculate` | `Invalid characters in expression` | native tool validation | static path |
| `cancel_pending_order` | `Non-pending order cannot be cancelled`; `Invalid reason` | domain precondition | static path |
| `exchange_delivered_order_items` | non-delivered status, item-list length, missing/invalid item, payment constraints | domain precondition / validation | task 52 has a missing-number error |
| `modify_pending_order_address`, `modify_pending_order_items`, `modify_pending_order_payment` | non-pending status, item/payment constraints, gift-card balance | domain precondition / validation | task 37 has same-item error; task 61 payment-method error |
| `return_delivered_order_items` | non-delivered status, original payment method, missing item | domain precondition | task 82 has original-payment error |

The current controlled-retention predicate is intentionally narrow:

- native callable was allowed and invoked once;
- exception message is non-empty, at most 256 characters, and unchanged by
  the existing redactor;
- message is a `ValueError` from the native callable boundary.

If any predicate fails, the visible message remains `Tool execution failed.`
and the sidecar preserves only scrubbed metadata.  This is safe, but it is
not claimed evaluator-compatible.

## Deterministic matrix gates

1. Known safe lookup/generic errors must match the native `ToolMessage` in
   `id`, `role`, `requestor`, `error`, and `content`.
2. Sensitive and unexpected exceptions must keep the stable fallback, redact
   sidecar diagnostics, and never leak to visible content.
3. Parser/registry/schema rejects retain `dispatch_count=0` and are tested
   independently from native replay. The frozen Task 37 domain rule also
   retains `dispatch_count=0`, while replaying its approved public message.
4. A lifecycle may become `closed/succeeded` only after evaluator completion
   and durable `results.json` append; all later failures stay operational.
5. Gateway/provider transport faults are tested by controlled fault injection,
   not by a Retail task or by inventing a ToolMessage.

## Proposed gateway preflight set

Run one trial, one task at a time, with the frozen A1 model/configuration.
These are preflight artifacts, not A1-main metrics.

| Task | Purpose | Expected lane |
| --- | --- | --- |
| 68 | Wrong ZIP then correction; `User not found` | approved native lookup parity |
| 38 | User lookup plus observed `Order not found` in A0 | approved native lookup parity |
| 82 | Observed `Payment method should be the original payment method` | native domain-precondition parity |
| 61 | Observed `Payment method not found` | native lookup/validation parity |
| 37 | Same old/new item ID | native-equivalent A1 domain reject (`dispatch=0`, `origin=harness_reject`) |

Task 37 is a critical containment control: under A1, the approved
`modify_pending_order_items` constraint rejects before dispatch, but
materializes the frozen public native error text so evaluator replay can
compare the trajectory. It must not be counted as a native-dispatch
compatibility pass.

## Explicit non-claims

- This matrix does not make unknown exceptions evaluator-compatible.
- It does not add retry, repair, timeout, rollback, or recovery.
- It does not establish gateway transient-error behavior from a normal Retail
  task; that requires controlled fault injection.

# ARC-01 — Target A1 validation and containment path

## Frozen A1 scope

A1 is **validation and containment only**:

- no retry;
- no argument repair or coercive rewrite;
- no task ground-truth, expected-action or evaluator access;
- no policy-reasoning oracle;
- no rollback/recovery implementation;
- no change to native tool function behavior.

```text
raw model response
  │
  ▼
parser / normalizer
  │ emits ToolCallRequest or structured parse error
  ▼
registry + requestor validation
  ▼
signature/schema validation
  ▼
explicit versioned domain constraints
  ▼
harness decision: ALLOW | REJECT
  ├─ REJECT ─► HarnessError ─► structured ToolMessage-compatible result
  │
  └─ ALLOW
       ▼
     safe dispatcher
       ▼
     native Environment / RetailTools function
       ▼
     DispatchResult(success | native_error | serialization_error)
       ▼
     structured result/error
       ▼
     append-only ToolEvent telemetry
```

## Stage responsibilities

| Stage | Allowed responsibility | Explicitly forbidden in A1 |
|---|---|---|
| Parser | Preserve raw argument text; JSON-decode exactly once; require object arguments; retain raw-response reference/digest | Guess malformed JSON or ask the model again |
| Registry/requestor | Check non-empty known tool name, duplicate registry names at startup, and requestor/toolkit authorization | Redirect an unknown/wrong-requestor call to another tool |
| Schema | Validate required/extra fields and annotated JSON-compatible types against the same `Tool.params` model used for exposure | Silent casting or argument insertion/removal |
| Domain constraints | Apply only named, versioned, deterministic invariants independent of task ground truth | Decide which item/order/payment best satisfies user intent |
| Harness decision | Emit `ALLOW` or `REJECT` with stable reason and unchanged-request digest | Retry, repair, or call the evaluator |
| Safe dispatcher | On `ALLOW`, call the native environment exactly once and classify boundary outcome | Swallow/convert a validation rejection into a native call |
| Result/error | Preserve native content/error while adding structured stage/code metadata outside native raw fields | Rewrite successful native result |
| Telemetry | Append proposed, validated, dispatched and completed events using stable IDs | Feed telemetry back into model behavior in A1 |

## Minimum insertion points in v0.1.3

### Insertion point 1 — parser adapter

Current boundary:
`src/tau2/utils/llm_utils.py:221-250`, especially `json.loads` at `:238`.

Target: extract the model-choice-to-message conversion into a small parser
adapter. Native mode must call a compatibility implementation with byte-for-byte
equivalent output. A1 mode additionally returns structured parse failure and raw
reference. This insertion is necessary because malformed argument JSON currently
escapes before any `ToolCall` exists.

### Insertion point 2 — execution adapter

Current boundary:
`src/tau2/orchestrator/orchestrator.py:337-340`.

Target: replace only the direct call site with an injected executor interface:

```text
tool_executor.execute(tool_call, environment, execution_context)
```

The default `NativeToolExecutor` delegates directly to
`environment.get_response(tool_call)`. `A1ValidationExecutor` implements the
frozen path above. This preserves orchestration, message ordering, evaluator and
native environment behavior.

### Existing sources reused without semantic edits

- Registry source: `Environment.get_tools()` /
  `ToolKitBase.get_tools()` (`environment.py:77-83`,
  `toolkit.py:82-92`).
- Signature schema: each `Tool.params` dynamic Pydantic model
  (`tool.py:61-149`).
- Requestor routing: native assistant/user toolkit ownership in
  `Environment.make_tool_call` (`environment.py:128-155`).
- Native dispatch: `Environment.get_response` and bound Retail function remain
  the terminal native implementation.

## Low-modification strategy

1. Put all new contracts, validators, domain rules, executor and telemetry in a
   separate harness package; do not edit Retail tools, tasks, policy or evaluator.
2. Add only two narrow dependency seams to tau2: parser adapter and injected
   tool executor. Both default to native-compatible implementations.
3. Do not add A1 flags to core data models until contracts are frozen. Use an
   external A1 runner/config first, while preserving the same `run_task`
   construction and evaluator.
4. Keep harness metadata in sidecar telemetry. Do not add fields to native
   `results.json`; A0/A1 benchmark metrics continue to use native schema.
5. Pin tool-schema and domain-constraint hashes at run start. Fail preflight on
   duplicate names or unexpected schema drift.
6. Test native compatibility before A1: the default adapters must produce the
   same `ToolCall`, call count, arguments, `ToolMessage`, trajectory and DB state.

## Explicit domain constraints admitted to A1 v1

The only currently evidence-backed Retail cross-field candidate is:

`retail.modify_pending_order_items.old_new_distinct.v1`

For every aligned old/new pair, reject identical item IDs before native WRITE.
The native code reports this invariant at
`src/tau2/domains/retail/tools.py:464-466`. It must remain versioned and tested
against native behavior. All product ranking, correct order/item selection,
payment preference and confirmation semantics remain outside A1.

## Containment semantics

- `REJECT` means no native dispatcher invocation and no DB mutation.
- A rejected call yields one structured, tool-visible error compatible with the
  conversation loop plus sidecar `HarnessError`.
- `ALLOW` means exactly one native invocation in A1.
- A native `ToolMessage.error=true` remains a native error result; A1 may
  classify it for telemetry but may not retry it.
- Every proposed call has `attempt_index=0` in A1. Any value above zero is a
  protocol violation because execution retry belongs to A2.


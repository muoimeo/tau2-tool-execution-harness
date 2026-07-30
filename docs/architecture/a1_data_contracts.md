# ARC-01 — A1 data contracts v1

These contracts are architecture contracts, not runtime implementation. Fields
are snake_case, JSON-serializable and versioned. Unknown fields are rejected
unless a later contract version explicitly allows them.

## Shared types

```text
HarnessStage =
  parse | registry | requestor | schema | domain_constraint |
  decision | dispatch | native_tool | result | telemetry

Decision = allow | reject

DispatchStatus =
  not_dispatched | success | native_error |
  dispatcher_error | serialization_error
```

Stable IDs must not contain secrets. Canonical digests use UTF-8 JSON with
sorted keys and fixed separators.

## ToolCallRequest

Immutable representation of one proposed call before validation.

| Field | Type | Required | Contract |
|---|---|---:|---|
| `contract_version` | string | yes | `"tool-call-request/v1"` |
| `run_id`, `task_id` | string | yes | Link to scheduled episode |
| `trial_index`, `trial_seed`, `turn_index` | integer | yes | Frozen execution coordinates |
| `logical_call_id` | string | yes | Stable within episode; derived once |
| `provider_call_id` | string/null | yes | Provider ID if present |
| `requestor` | `"assistant"`/`"user"`/null | yes | Null only when parse fails before extraction |
| `tool_name` | string/null | yes | Original name, never normalized by guessing |
| `raw_arguments_text` | string/null | yes | Exact provider argument text when available |
| `arguments` | object/null | yes | Parsed object; null on parse failure |
| `raw_response_ref` | string/null | yes | Sidecar reference, not full sensitive payload |
| `raw_response_sha256` | string/null | yes | Integrity link where raw capture exists |
| `request_sha256` | string | yes | Digest over immutable identity/name/raw arguments |
| `created_at` | RFC-3339 string | yes | Observation time |

Invariant: validators may not mutate this object. A repaired/coerced call would
require a different future contract and is forbidden in A1.

## ValidationResult

| Field | Type | Required | Contract |
|---|---|---:|---|
| `contract_version` | string | yes | `"validation-result/v1"` |
| `logical_call_id`, `request_sha256` | string | yes | Must match request |
| `decision` | `allow`/`reject` | yes | Final A1 decision |
| `failed_stage` | `HarnessStage`/null | yes | Null only for allow |
| `failure_code` | string/null | yes | RES-06 code for reject |
| `violations` | array | yes | Stable objects: code, path, message, constraint_id |
| `validator_versions` | object | yes | Parser/registry/schema/domain validator versions |
| `tool_schema_sha256` | string/null | yes | Schema used for validation |
| `domain_constraints_sha256` | string/null | yes | Frozen rule-set digest |
| `arguments_unchanged` | boolean | yes | Must be true in A1 |
| `validated_at` | RFC-3339 string | yes | Decision time |

Invariants:

- `allow` requires no violations and no failure code.
- `reject` requires at least one violation.
- `arguments_unchanged` must always be true.

## HarnessError

Structured containment record. It is sidecar metadata and may be rendered into
a safe tool-visible message.

| Field | Type | Required | Contract |
|---|---|---:|---|
| `contract_version` | string | yes | `"harness-error/v1"` |
| `error_id`, `logical_call_id` | string | yes | Stable linkage |
| `stage`, `code` | string | yes | Stable taxonomy location/code |
| `message` | string | yes | Safe deterministic message |
| `details` | object | yes | Non-secret structured context |
| `retryable` | boolean | yes | Must be false for all A1 rejections |
| `repairable` | boolean | yes | Must be false in A1 |
| `cause_type` | string/null | yes | Exception class if safely observed |
| `cause_message_redacted` | string/null | yes | Never store credentials/headers |
| `created_at` | RFC-3339 string | yes | Creation time |

`INSUFFICIENT_EVIDENCE` is an analysis disposition, not a runtime HarnessError
code.

## DispatchResult

One outcome for one allowed request or contained rejection.

| Field | Type | Required | Contract |
|---|---|---:|---|
| `contract_version` | string | yes | `"dispatch-result/v1"` |
| `logical_call_id`, `request_sha256` | string | yes | Request linkage |
| `validation_decision` | `allow`/`reject` | yes | Copied from validation |
| `dispatch_attempted` | boolean | yes | False for rejection |
| `dispatch_count` | integer | yes | A1 permits only 0 or 1 |
| `status` | `DispatchStatus` | yes | Outcome |
| `native_tool_message` | object/null | yes | Unmodified native-compatible result |
| `harness_error` | `HarnessError`/null | yes | Present for contained rejection/dispatcher boundary error |
| `started_at`, `completed_at` | RFC-3339/null | yes | Null start allowed when not dispatched |
| `latency_ms` | integer/null | yes | Dispatcher boundary latency |
| `state_before_sha256`, `state_after_sha256` | string/null | yes | Passive state integrity when available |

Invariants:

- `reject` ⇒ `dispatch_attempted=false`, `dispatch_count=0`,
  `status=not_dispatched`.
- `allow` ⇒ `dispatch_attempted=true`, `dispatch_count=1`.
- A1 must never emit `dispatch_count>1`.

## ToolEvent

Append-only telemetry envelope. One logical call produces ordered phase events.

| Field | Type | Required | Contract |
|---|---|---:|---|
| `contract_version` | string | yes | `"tool-event/v1"` |
| `event_id`, `run_id`, `logical_call_id` | string | yes | Correlation |
| `sequence` | integer | yes | Monotonic within logical call |
| `phase` | string | yes | `proposed`, `parsed`, `validated`, `dispatch_started`, `dispatch_completed`, `result_emitted` |
| `attempt_index` | integer | yes | Always 0 in A1 |
| `tool_name`, `requestor` | string/null | yes | Copied, not guessed |
| `decision`, `failure_code`, `dispatch_status` | string/null | yes | Phase-appropriate |
| `request_sha256` | string | yes | Immutable request linkage |
| `payload_ref` | string/null | yes | Reference to contract object; avoid duplication |
| `timestamp` | RFC-3339 string | yes | Observation time |
| `telemetry_schema_version` | string | yes | Sidecar schema version |

Minimum event sequences:

```text
ALLOW:  proposed → parsed → validated → dispatch_started
        → dispatch_completed → result_emitted

REJECT: proposed → parsed? → validated → result_emitted
```

Parser rejection may omit `parsed` but must retain `proposed`, `validated` and
`result_emitted`.

## Native compatibility mapping

| Native v0.1.3 field | A1 contract |
|---|---|
| `ToolCall.id` | `provider_call_id`; used in logical ID derivation |
| `ToolCall.name` | `tool_name` |
| `ToolCall.arguments` | `arguments` |
| `ToolCall.requestor` | `requestor` |
| `ToolMessage.id/content/requestor/error` | preserved inside `native_tool_message` |
| `AssistantMessage.raw_data` | referenced by `raw_response_ref` only when successfully captured |

Native `results.json` remains the benchmark record. These contracts live in
sidecar telemetry so A0 and A1 native metrics remain comparable.


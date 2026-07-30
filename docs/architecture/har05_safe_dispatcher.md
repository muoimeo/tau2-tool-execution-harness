# HAR-05 — safe dispatcher and structured errors

## Boundary

`NativeToolExecutor` owns one bound native callable and permits one execution
per `(run, task, trial, seed, turn, logical_call_id)` context. `A1ValidationExecutor` materializes an already validated
parsed request or an HAR-03 `ParserFailure`. Neither class performs registry
lookup, schema validation, retry, repair, rollback, recovery, timeout handling,
output validation, telemetry writing, environment/orchestrator integration, or
gateway I/O.

## Reject materialization

A validation reject or parser failure never reaches `NativeToolExecutor`.
It produces `DispatchResult(validation_decision=reject,`
`dispatch_attempted=false, dispatch_count=0, attempt_index=0)` and a
native-compatible `ToolMessageRecord(role="tool", error=true)`. Parsed calls
retain their original logical call ID; parser failures retain the ARC-01A-v2
synthetic ID. Both paths create a deterministic structured `HarnessError` and
stable content: `Tool call rejected: <CODE>.`

With a state supplier, reject snapshots are taken before and after with no
callable invocation; `DispatchResult` enforces equal hashes.

## Allow materialization

Allow calls decode fresh canonical arguments and invoke the supplied callable
once. Native output is serialized into native-compatible tool content; no output
semantics are evaluated. A duplicate logical call ID raises
`DuplicateExecutionError(EXECUTION_DUPLICATE_DISPATCH)` before a second call.

Native `Exception` subclasses are contained as `UNKNOWN_EXECUTION_ERROR`, with
`retryable=false`. Sidecar `HarnessError` retains the exception class plus a
bounded redacted message; visible content is only `Tool execution failed.`
No domain-precondition is reclassified from a string message.

# HAR-07D — trace and lifecycle runtime propagation

**Scope statement:** A1 v1 covers **assistant-originated orchestrated
`LLMAgent` tool calls only**. User-originated tool calls remain on native tau2
execution and must not be reported as harness-covered telemetry or metrics.

`HarnessRuntimeBridge` emits HAR-06 JSONL events without changing a registry,
schema/domain, or dispatch decision. Valid calls emit `proposed`, `parsed`,
`validated`, durable `dispatch_started`, `dispatch_completed`, then
`result_emitted`. Validation rejects omit both dispatch phases. Parser
rejections emit `proposed`, `parsed`, and `result_emitted` only.

`dispatch_started` is appended, flushed and fsynced before the native callable.
A failure before that point records `failed_before_dispatch` best-effort and
raises to the supervisor. A failure after a callable returns records
`failed_after_dispatch` with `side_effect_may_have_occurred=true`, does not
rollback or retry, and raises. If lifecycle storage itself is unwritable, the
primary telemetry exception is retained and a secondary operational message is
written to stderr.

On normal orchestrator completion, lifecycle closes with trace SHA-256, event
count, last sequence, identity/attempt and `succeeded` outcome. Lifecycle
writes use temp file, flush, fsync and replace; this provides file replacement
atomicity under the platform contract, while directory durability remains
filesystem-dependent.

The native orchestrated path already calls `sync_tools()` inside successful
`Environment.get_response()` and once at the end of `Orchestrator.step()`.
The A1 wrapper reproduces this: one `make_tool_call`, one underlying callable,
and the same two sync calls. It introduces no additional dispatch or state
transition.

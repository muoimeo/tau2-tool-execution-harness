# HAR-07 integration amendment v1 (pre-runtime)

HAR-07 remains In Review. Only an explicit `OrchestratedHarnessContext` enables
HAR-03 parsing; evaluator, NL assertion, and interface callers retain native
`generate()` behavior. Runtime integration must pass raw provider arguments to
HAR-03 before native JSON parsing.

The context is constructed only by the orchestrated `LLMAgent` route and
contains run ID, task ID, trial index, trial seed, turn index, and the trusted
requestor established by that route. It is not inferred from provider payload.
`None` selects the untouched native parser path. This is intentionally a
scoped hook design, rather than a global replacement of `llm_utils.generate()`.

Parser failures use an episode-scoped sidecar keyed by synthetic ID. The native
`ToolCall` envelope contains only ID, recovered name or
`__harness_invalid_tool_call__`, empty arguments, and requestor. Orchestrator
must inspect this sidecar before registry/schema/dispatch.

The synthetic call is a native routing envelope, not a HAR `ToolCallRequest`.
It has the HAR-03 synthetic ID, recovered provider tool name when available (or
the reserved invalid name), `{}` arguments, and trusted requestor. It never
carries malformed raw JSON or the full `ParserFailure`. A sidecar hit skips
registry lookup, schema/domain validation, and native dispatch, then HAR-05
materializes the error through the single ToolMessage adapter.
The sidecar constructor requires the full episode-turn context and rejects a
failure whose run/task/trial/seed/turn/requestor does not exactly match it.

Each episode attempt owns its registry, bindings, state supplier, duplicate
guard, trace and lifecycle files. Lifecycle writes are atomic and fsynced.
Closed attempts validate trace checksum/count and skip; incomplete attempts are
marked aborted and rerun from a clean initial state under a new attempt ID. An
`open()` lifecycle must be atomically persisted before generation. A completed
attempt can never be overwritten. Resume is episode-granular: it never resumes
a partially executed episode or reuses an in-memory duplicate guard.

Trace failure is fail-fast research-integrity policy: before dispatch no native
call; after dispatch record `failed_after_dispatch` plus
`side_effect_may_have_occurred=true`, then abort later work. No rollback/retry.
Trace appends flush and fsync in A1. Runtime must preflight the trace path and
must write `dispatch_started` successfully before calling a native tool.

## Explicit exclusions

This amendment specifies integration behavior only. It does not authorize
retry, repair, timeout, rollback, recovery, output validation, postcondition
validation, gateway changes, evaluator changes, or Retail-tool changes.

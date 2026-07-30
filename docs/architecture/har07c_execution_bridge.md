# HAR-07C — native message and execution bridge

An `Orchestrator` receives an optional `EpisodeHarness`. It fails before an
episode starts if an `LLMAgent` has explicitly enabled HAR-03 but no harness
execution bridge is supplied. With an episode harness, the orchestrator owns
the agent parser opt-in for its lifetime and clears it in `finally` at the end
of `run()`.

At the assistant-to-environment branch, `HarnessRuntimeBridge` is invoked
before `Environment.get_response()`. The bridge first checks membership of the
call ID in the episode-turn `ParserFailureSidecar`; the synthetic tool name is
never a routing signal. A hit is consumed exactly once and materialized through
HAR-05 as a native-compatible error ToolMessage. It performs no registry,
schema, domain, or callable operation.

For any non-sidecar assistant call, the bridge reconstructs an immutable
`ToolCallRequest` from the already parsed native envelope, applies HAR-02
registry/requestor validation, HAR-04 strict schema/domain validation, then
HAR-05 dispatch. Registry/schema/domain rejects dispatch zero times. Allows
use a factory-bound wrapper around native `Environment.make_tool_call()` and
call `Environment.sync_tools()` only after success, preserving native tool
semantics. Exceptions are contained by HAR-05.

The only conversion from contract result to tau2 data model is
`to_native_tool_message(ToolMessageRecord)`. The harness-disabled branch stays
on the original `Environment.get_response()` call.

HAR-07C intentionally excludes trace/lifecycle propagation, retry, timeout,
repair, rollback, recovery, gateway traffic, and live episodes. Those remain
for HAR-07D/E.

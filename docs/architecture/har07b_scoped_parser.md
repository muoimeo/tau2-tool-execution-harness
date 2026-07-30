# HAR-07B — scoped parser integration

`tau2.utils.llm_utils.generate()` has two paths. With no
`OrchestratedHarnessContext`, it retains native response parsing and does not
construct HAR objects. A context requires an episode-turn
`ParserFailureSidecar`; raw provider function arguments then enter HAR-03
before the bridge constructs native `ToolCall` objects.

Only `LLMAgent.enable_harness_parser()` supplies this opt-in pair. The user
simulator, NL assertion evaluator, interface agent, and all unmodified callers
continue to call `generate()` without it.

Valid calls preserve provider order, ID, tool name, requestor, and object
arguments. The second JSON decode is of HAR-03's canonical `arguments_json`,
not raw provider arguments. Malformed calls become an empty-argument synthetic
routing envelope, while their structured `ParserFailure` stays in the
episode-turn sidecar. The bridge performs no registry/schema/domain validation,
ToolMessage construction, dispatch, or environment access.

`ParserFailureSidecar` exposes only `consume()`, which is context-checked and
destructive: unknown, already consumed, duplicate registration, or context
mismatch fail closed.
The native `Orchestrator` does not yet consume this sidecar; that is HAR-07C.

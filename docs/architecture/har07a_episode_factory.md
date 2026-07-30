# HAR-07A — episode factory and lifecycle binding

`EpisodeHarnessFactory` is the sole constructor for HAR components belonging to
one A1 episode attempt. It receives a callable that constructs a new native
`Environment`; it does not accept a reusable environment instance. For each
attempt, it creates a registry snapshot, immutable native-callable bindings,
one shared `DuplicateDispatchGuard`, per-tool `A1ValidationExecutor`s, an
episode `TraceLogger`, and an open lifecycle record.

`OrchestratedHarnessContext` and `ParserFailureSidecar` are generated per
episode turn and requestor. The sidecar itself validates the full context, so a
failure cannot be registered under a different task, trial, seed, turn, or
requestor.

On a pre-existing lifecycle path, a closed attempt is checksum/count validated
and returned as `ClosedEpisodeSkip`. An incomplete attempt is atomically marked
`aborted`; the factory constructs a distinct `episode_attempt + 1` using a new
environment factory call. Mid-episode continuation is not an API.

This stage is not connected to `run_task`, `LLMAgent`, `llm_utils.generate`, or
`Orchestrator`. It does not execute a native tool, make gateway traffic, or
change an evaluator.

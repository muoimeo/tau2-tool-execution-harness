# EXP-02 A1 validation-harness entrypoint

## Activation

The native path is the default.  A1 is activated only with `--a1-harness`.
It supports `agent=llm_agent` and requires `--max-concurrency 1`.

`--a1-trace-root` is optional.  If it is omitted, the runner derives a
deterministic sibling directory from `--save-to`:

```text
<results-parent>/<results-stem>.a1-traces/
  a1-run-config.json
  task-<task>__trial-<trial>__seed-<seed>__attempt-<attempt>.jsonl
  task-<task>__trial-<trial>__seed-<seed>__attempt-<attempt>.lifecycle.json
```

The A1 config sidecar is canonical JSON and is written once. A later run using
the same trace root with a different frozen A1 configuration fails before an
episode runs.

## Lifecycle

For every scheduled assistant episode, `run_task` asks the A1 entrypoint to
create a fresh `EpisodeHarness`. The factory owns the fresh Environment,
registry snapshot, parser sidecars, duplicate guard, trace logger and
lifecycle. The created Environment is then used to construct `LLMAgent`, user
and `Orchestrator`, so all execution bindings refer to that one episode.

A closed latest attempt is hash/count-validated then skipped only when the
native results resume set already contains the same `(task, trial, seed)`.
An incomplete latest attempt is marked aborted by the factory and rerun from a
fresh Environment with incremented `episode_attempt`; it is never resumed in
the middle.

## Explicit exclusions

This entrypoint does not add retry, repair, rollback, recovery, output
validation, evaluator changes, task-data changes or gateway behavior.

# HAR-06 — append-only ToolEvent telemetry

`TraceLogger` writes canonical UTF-8 JSONL, one `ToolEvent` per line. Event
keys and JSON serialization are deterministic; object keys are sorted and each
stream sequence increments only after a successful append.

For parsed calls, valid phase paths are:

```text
proposed → parsed → validated → dispatch_started → dispatch_completed → result_emitted
proposed → parsed → validated → result_emitted                 (validation reject)
```

For HAR-03 parser failures, the valid path is:

```text
proposed → parsed → result_emitted
```

It cannot enter registry/schema/dispatch. Duplicate phase, missing phase, or a
reject dispatch phase raise `TraceInvariantError`. File read/write errors raise
`TraceLoggerError`; they do not mutate an existing DispatchResult, consume an
event sequence, or silently continue.

Each event records run/task/trial/seed/turn, call/request IDs, requestor, tool
name/type, decision/failure, dispatch count/attempt index, state hashes, and
redacted exception metadata. Default retention is `raw_retention="none"`.
Raw argument content is permitted only when `fixture_raw_retention=True`; this
mode is exclusively for deterministic local fixtures, never live telemetry.

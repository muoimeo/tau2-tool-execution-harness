# HAR-01 to HAR-05 contract review before HAR-06

## Findings fixed during review

1. **HAR-05 duplicate guard scope:** the initial guard keyed only a logical
   call ID, which could falsely block a provider ID reused in another trial.
   It now keys `(run_id, task_id, trial_index, trial_seed, turn_index,
   logical_call_id)`. A regression proves the same provider ID in a different
   trial is allowed while a repeat in the same call context is blocked.

2. **HAR-01 ToolEvent scope:** the initial event model lacked task/trial/turn,
   tool type, dispatch count, state hashes, and exception fields required for
   telemetry. HAR-06 extends the immutable contract and validates raw-retention
   mode at model level.

## No blocker found for HAR-06

The sequence from parser through dispatcher is coherent for the implemented A1
scope: parser failures create no ToolCallRequest dispatch; registry/schema/domain
rejects dispatch zero times; allow executes once; native exceptions are
contained; no retry/repair/rollback is present. HAR-06 does not alter those
results.

## Remaining integration risks (not hidden)

- Native provider adapter ordering and actual ToolMessage insertion are still
  outside the standalone components.
- Exception-message redaction is pattern-based (email/order ID/long numbers),
  not a complete semantic PII classifier; live telemetry must remain sidecar
  restricted and use no raw retention.
- TraceLogger is a synchronous append-only component. Integration must decide
  whether `TraceLoggerError` terminates/contains an episode or is surfaced to
  the experiment supervisor; HAR-06 intentionally does not choose that runtime
  policy.
- State hashes are available only when the caller binds a state supplier; a
  missing supplier yields null hashes, not a synthetic claim of state safety.

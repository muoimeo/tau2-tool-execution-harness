# Provider Observability v1

`provider-observability/v1` is an A2 sidecar-only contract. It does not change provider retry eligibility/budget, lifecycle status transitions, unsafe-resume authorization, `SimulationRun`, evaluator behavior, or `results.json`.

Each JSONL row represents one harness-owned provider invocation and records episode/generation identity, a SHA-256 request fingerprint, safe request-shape counts/buckets, start/end timestamps, latency, provider/model IDs, response shape, token usage, and terminal outcome. Missing provider metadata is represented as `null`.

The request fingerprint is SHA-256 over canonical JSON containing model, messages, tool schemas, tool choice, and non-secret generation arguments. API keys, API base URLs, base URLs, and raw headers are excluded. The canonical bytes exist only in memory; sidecars retain only the digest.

No prompt, message content, tool arguments, raw request, raw response, or raw headers are persisted by this contract.

When a provider failure exhausts after a completed WRITE, `unsafe-provider-failure-audit/v1` is written after `failed_operational` terminalization. It summarizes dispatch counts, WRITE names, state-hash transitions, provider fingerprints/IDs/timing, integrity checks, environment persistence capability, and a suggested authorization identity. It never grants authorization.

Client telemetry can establish request identity, timing, returned shape/IDs, and reported routing metadata. It cannot establish gateway cache/dedup decisions, actual backend routing, raw upstream completion, gateway transformation, or backend model revision.

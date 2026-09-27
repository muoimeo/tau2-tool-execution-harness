# A2 — provider retry and observability

Read this folder when the question is: “The model provider failed before there
was a valid assistant message; can the generation safely be retried and audited?”

- `retry.py` owns typed provider failures, bounded retry, backoff, and the
  provider-attempt JSONL sidecar.
- `model_response_inspector.py` records privacy-preserving structure for an
  unusable provider response; it does not change the response.
- `provider_observability.py` writes redacted unsafe-failure audit packets.

These modules never retry a native tool call, a domain error, or a harness
validation reject. Tool execution remains in `../a1/`.

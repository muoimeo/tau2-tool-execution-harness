# HAR-03 — standalone parser and normalizer

## Frozen behavior

`ParserNormalizer` accepts a caller-provided ordered sequence of `RawToolCall`
records and a pre-validated `TrustedRouteContext`. It decodes each raw argument
string once with `json.loads`; only a decoded JSON object becomes a
`ToolCallRequest`. The original string remains in the normalized request.

When both provider call structure and raw JSON are invalid, the raw string is
still decoded once, but `PARSE_RESPONSE_STRUCTURE_INVALID` takes precedence:
there is no trustworthy native call identity to normalize.

Malformed JSON, a valid non-object JSON value, or missing native provider call
identity/name produce `ParserFailure`, not `ToolCallRequest`. A failure retains
the raw call, full request SHA-256, zero-based malformed ordinal, and the
ARC-01A-v2 synthetic ID. It does not create `ToolMessage`.

If trusted route metadata is missing or invalid, parsing stops before the raw
arguments are decoded. The batch returns only
`PARSE_REQUESTOR_METADATA_INVALID`; it creates no request or parser failure and
does not guess a requestor/toolkit.

Synthetic ID reuse in one batch is checked against full failure request hashes.
A same truncated ID with different full hashes raises the fail-closed
`PARSE_SYNTHETIC_ID_COLLISION` error.

## Explicit exclusions

HAR-03 imports no registry and performs no tool lookup, schema/domain
validation, dispatch, ToolMessage construction, model generation, Environment
call, or gateway I/O.

## Unresolved multi-call ordering at integration

The contract requires the future provider adapter to supply raw calls in the
provider response's received array order. Parser failures obtain ordinals among
malformed members only; valid calls do not consume an ordinal. The current
native LiteLLM response adapter has not yet been integrated or instrumented,
so the exact extraction behavior for malformed provider structures (including
an absent/non-array `tool_calls`) remains unresolved. Integration must map a
response-level structural failure to at most one raw call at input position 0,
then preserve this contract before invoking HAR-03.

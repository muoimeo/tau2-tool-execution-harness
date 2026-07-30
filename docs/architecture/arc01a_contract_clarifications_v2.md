# ARC-01A contract clarifications v2

This document supersedes only the synthetic-ID, requestor, strict-schema and
state-hash details in `arc01_contract_amendment_v1.md`.

## 1. Synthetic parser-rejection ID

Canonical input is UTF-8 bytes of the following NUL-delimited fields, with no
whitespace normalization or omitted separators:

```text
toolcall-parse-v2\0
run_id\0
turn_index_decimal\0
malformed_call_ordinal_decimal\0
provider_call_id_or_empty\0
requestor\0
raw_tool_name_or_empty\0
raw_arguments_text_or_empty
```

ID is `hparse_` plus first 32 lowercase hexadecimal SHA-256 characters.
`malformed_call_ordinal` is zero-based among malformed calls in one provider
response/turn. It prevents collisions where malformed calls have otherwise
identical fields. The future parser must enumerate the provider's raw tool-call
array in received order; each malformed member receives the next ordinal. When
no array member can be identified, it emits at most one response-level
rejection with ordinal `0`.

A full 256-bit request SHA-256 is also retained in the `ToolCallRequest`. The
future integration must maintain a per-turn `logical_call_id -> request_sha256`
map: reuse is permitted only for byte-identical requests; the same truncated ID
with a different full request hash is a fail-closed containment error, never
silently merged or dispatched.

## 2. Requestor fallback

`requestor` is not recovered from malformed provider arguments. It is supplied
by the parser caller as trusted route context: `assistant` for the agent's
model-response path and `user` for a user-tool model-response path. It is
required before parsing begins.

If caller context is absent/invalid, parser creates no `ToolCallRequest`, does
not choose a toolkit, and returns a structured
`PARSE_REQUESTOR_METADATA_INVALID` boundary error to its caller. The caller
must contain the episode using its already-known participant route; it must not
guess `assistant` or `user` from tool name/content. HAR-01 represents this code
but does not implement that caller boundary.

## 3. Strict schema boundary

HAR-04 will validate a **per-call** native dynamic parameter model using:

```python
params_model.model_validate(arguments, strict=True, extra="forbid")
```

It must not set `params_model.model_config`, call `model_rebuild`, replace
`Tool.params`, modify `Tool.openai_schema`, or mutate any global Pydantic
configuration. The policy is represented by immutable
`SchemaValidationPolicy(strict=True, extra="forbid",
mutate_native_params_model=False)` in HAR-01.

## 4. State-hash canonicalization

A1 deterministic WRITE tests hash the entire `environment.tools.db` Pydantic
dump with `mode="json"`; scoped state is not admitted in v1. Serialization is:

- UTF-8;
- JSON object keys recursively sorted by Unicode string ordering;
- compact separators `,` and `:`;
- `ensure_ascii=False`;
- finite JSON numbers only; `NaN`/infinity and non-JSON values reject;
- lists preserve order exactly and are never sorted/deduplicated;
- volatile field exclusions are an empty tuple in v1.

Thus any volatile-field exclusion, scoped hash, snapshot/postcondition claim or
live-run state hash requires a new contract version and explicit approval.

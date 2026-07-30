# HAR-02 — registry and requestor validation contract

## Scope

HAR-02 is an independent, non-dispatching layer. It reads native
`ToolKitBase.get_tools()` and `ToolKitBase.tool_type()` metadata solely to build
an immutable snapshot. It does not import or invoke `Environment`, model
generation, an orchestrator, `Tool.__call__`, or the gateway.

## Snapshot record and hash

Each `RegistryTool` records:

- `tool_name`;
- one `allowed_requestor` namespace (`assistant` or `user`);
- toolkit class identity as `module:qualname`;
- params-model class identity as `module:qualname`;
- canonical JSON params schema and its SHA-256;
- native `ToolType` value (`read`, `write`, `think`, `generic`) when native
  metadata is available.

The snapshot serializes only these data fields. It never serializes `_func`, a
bound Python method, object address, or database contents. Entries are sorted
by `(allowed_requestor, tool_name, toolkit_identity)` before canonical JSON
serialization and SHA-256. Therefore insertion order cannot affect
`registry_sha256`.

## Fail-closed rules

- A duplicate `(requestor, tool_name)` observed during build raises
  `RegistryPreflightError` with `PREFLIGHT_DUPLICATE_TOOL_NAME`; it produces no
  snapshot and never selects a last entry.
- A trusted route context mismatch rejects at `requestor` with
  `REGISTRY_WRONG_REQUESTOR`.
- A tool absent from the caller's requestor namespace but present in the other
  namespace rejects at `registry` with `REGISTRY_WRONG_REQUESTOR`.
- A tool absent from every namespace rejects at `registry` with
  `REGISTRY_UNKNOWN_TOOL`.
- An allow result must record passed stages exactly as `requestor`, then
  `registry`, and contain the resolved immutable snapshot entry.

## Deliberate unresolved items

Retail currently supplies assistant tools only; the user-tool path is tested
against a minimal native `ToolKitBase` fixture, not inferred to exist in Retail.
Runtime ownership/lifetime of a snapshot, live registry drift detection, and
conversion of a rejection to native `ToolMessage(error=True)` are deferred to
the approved integration stage. HAR-02 has no dispatch method and no runtime
hook.

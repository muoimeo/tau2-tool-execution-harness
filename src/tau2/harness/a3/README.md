# A3 — bounded recovery and post-dispatch checks

A3 is opt-in (`--a3-recovery`) and requires the existing A1 harness and A2
provider-retry mode. With A3 off, the A0/A1/A2 execution path is unchanged.

Implemented A3 v1 capabilities are deliberately narrow:

- one lossless repair attempt after an A1 parser failure only: Markdown JSON
  fences, double-encoded JSON objects, or a JSON trailing comma;
- raw native-output contract validation before a successful `ToolMessage` is
  materialized;
- pre/post snapshots and explicit Retail WRITE postconditions for the frozen
  allowlist; and
- quarantine after an ambiguous or invalid WRITE, without rollback or replay.

It does **not** perform model-assisted repair, semantic re-planning, native
tool retry, rollback, generic state recovery, or automatic WRITE replay.

# Execution harness layout

`a1/` contains validation and containment: parser, registry, strict schema and
domain validation, safe dispatch, trace, episode lifecycle, and runtime bridge.

`a2/` contains only provider-generation retry and provider diagnostics. It does
not retry tools or validation rejections.

`a3/` contains narrow recovery: one-shot representation repair after an A1
parser failure, output-contract checks, selected Retail WRITE postconditions,
quarantine, and recovery audit. It does not replay a native WRITE.

The Python files at this directory level are compatibility aliases. Existing
imports such as `tau2.harness.runtime` continue to work while new code should
use `tau2.harness.a1.runtime` or `tau2.harness.a2.retry`.

# ARC-01A — A1 test matrix v1

This is a test-first specification only. It does not integrate a harness into
`llm_utils.generate` or `Orchestrator.step`, call a gateway, or alter native
tau2 source.

| ID | Layer | Fixture / deterministic setup | Expected assertion | Status before HAR runtime |
|---|---|---|---|---|
| ARC-E01 | Native evidence | `tests/test_arc01_native_error_budget.py` | Two tool errors leave `num_errors=0`; no `too_many_errors` termination | executable now |
| PAR-01 | parser | `parser_rejection_spec.json:malformed_json` | Stable synthetic ID; `PARSE_MALFORMED_JSON`; no dispatch | specification only |
| PAR-02 | parser | `arguments_not_object` | `PARSE_ARGUMENTS_NOT_OBJECT`; assistant requestor retained | specification only |
| PAR-03 | parser | `invalid_response_structure_without_call_id` | stable synthetic ID without provider ID; no dispatch | specification only |
| REG-01 | registry | unknown tool fixture | `REGISTRY_UNKNOWN_TOOL`; `dispatch_count=0` | specification only |
| REQ-01 | requestor | non-null but unauthorized requestor/toolkit pair | `REGISTRY_WRONG_REQUESTOR`; `dispatch_count=0` | specification only |
| SCH-01 | schema | missing required field | `SCHEMA_MISSING_REQUIRED_ARG`; no coercion/dispatch | specification only |
| SCH-02 | schema | extra field case in `rejection_contract_examples.json` | `SCHEMA_EXTRA_ARG`; strict + `extra=forbid`; no dispatch | specification only |
| SCH-03 | schema | wrong typed field | `SCHEMA_WRONG_ARG_TYPE`; no coercion/dispatch | specification only |
| DOM-01 | domain rule | equal old/new pair case | `SCHEMA_CONSTRAINT_VIOLATION`; no dispatch, no WRITE state hash change | specification only |
| ALW-01 | clean READ | `clean_allow_contract_examples.json:read_allow_preserves_arguments` | exact canonical arguments, one native dispatch, native result preserved | specification only |
| ALW-02 | clean WRITE | `write_allow_uses_one_native_dispatch` on deterministic local DB | one native dispatch; before/after state hash records execution | specification only |
| SAFE-01 | reject WRITE | deterministic valid-shaped but rejected request | `dispatch_count=0`; pre/post DB hash equal | specification only |
| TEL-01 | telemetry | every case above | ordered ToolEvents, attempt index zero, redaction policy preserved | specification only |
| CMP-01 | native adapter | valid fixture through `NativeToolExecutor` vs direct `Environment.get_response` | equal ToolMessage fields and one native call | specification only |

## Execution gates

1. Implement contracts/parser/registry/schema as pure units and turn PAR/REG/
   SCH tests executable.
2. Implement safe dispatcher and turn DOM/ALW/SAFE/TEL/CMP tests executable on
   deterministic local fixtures.
3. Pass all deterministic tests before HAR-07 integration.
4. Run a no-gateway integration smoke only after harness injection is approved.
5. Run a live A1 preflight only after the EXP-01B checklist has no blocking
   differences. Live-model trajectory variance is not used as deterministic
   compatibility evidence.


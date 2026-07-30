# HAR-07E deterministic runtime verification and clean-path parity

All entries below are local deterministic tests with the same scripted episode,
task ID `71`, trial index `0`, seed `300`, fresh initial DB and tool calls. No
gateway, model provider, Retail tool, evaluator, reward, or ground-truth data
is involved.

| Case | Native vs A1 result | Classification | Deterministic evidence |
|---|---|---|---|
| Valid READ | ToolMessage fields/order, call count, DB, conversation and `agent_stop` termination match; A1 trace/lifecycle close valid | PASS | `test_har07_clean_path_parity.py` |
| Valid WRITE | Same parity; exactly one state mutation | PASS | `test_har07_clean_path_parity.py` |
| Clean multi-call | Ordered ToolMessages and final DB match; no synthetic IDs/artifacts | PASS | `test_har07_clean_path_parity.py` |
| Parser rejection | A1 emits structured synthetic rejection without dispatch; native raw parser would raise before a tool loop | Allowed A1 containment divergence | `test_har07_runtime_bridge.py`, `test_har07_trace_runtime.py` |
| Registry/requestor rejection | A1 produces safe error with zero dispatch; native has no registry containment | Allowed A1 validation divergence | `test_har07_runtime_bridge.py` |
| Schema/domain rejection | A1 produces safe error with zero dispatch/state change | Allowed A1 validation divergence | `test_har07_runtime_bridge.py` |
| Mixed valid/malformed calls | A1 preserves provider order and contains malformed calls while dispatching only valid calls | Allowed A1 containment divergence | `test_har07_runtime_bridge.py`, `test_har07_scoped_parser.py` |
| Native callable exception | A1 preserves one invocation and state outcome but emits safe generic visible error instead of native exception text | Allowed A1 safety divergence | `test_har07_runtime_bridge.py` |
| User-originated tool call | Remains native; excluded from A1 coverage | PASS scope boundary | `test_har07_trace_runtime.py` |

For valid clean calls, A1 emits six events per call:
`proposed → parsed → validated → dispatch_started → dispatch_completed → result_emitted`.
Lifecycle closes with event count, last sequence, SHA-256 and succeeded outcome.

This report establishes clean-path parity for the deterministic scripted matrix,
not model/gateway parity. Gateway preflight remains a separate later stage.

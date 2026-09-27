# Controlled Fault Injection FI-v1

FI-v1 is a local, deterministic execution-layer experiment. It does not use a
gateway, prompt an LLM to fail, or change raw A0/A1/A2 artifacts.

```text
LLMAgent / UserSimulator
  -> tau2.utils.llm_utils.generate
  -> litellm.completion
  -> loopback OpenAI-compatible scripted server
  -> LiteLLM ModelResponse
  -> native parser, or HAR-03 -> A1/A2 validation
  -> native Environment or HAR-05 dispatcher
```

The frozen source is a successful A0 transcript. `fixtures.compile_fixture`
removes `raw_data` and reasoning fields, producing an independent fixture with
separate agent and user response queues. The same fixture hash is used in
A0-F, A1-F and A2-F; the provider-boundary fault is the only planned change.

The FI-v1 headline observation fields are: disposition, dispatch/WRITE count,
state-hash evidence, provider attempts/recovery, and invariant violations.
Scenario coverage is experiment QA, not a headline quality metric.

At execution, the FI runner uses an in-memory copy of the selected Retail task
with `evaluation_criteria=None`. This prevents a deliberately malformed call
from being confused with a task-evaluator mismatch. It does not write or modify
the Retail task file; FI observations are not benchmark reward metrics.

## Exact Retail controls

- **D1** sends `modify_pending_order_items` with aligned `item_ids[i] ==
  new_item_ids[i]`. Native Retail code raises `ValueError("The new item id
  should be different from the old item id")`; A1/A2 must reject before
  dispatch with that native-equivalent public message.
- **D2** sends `find_user_id_by_name_zip` with a non-matching first name, last
  name and zip. Native Retail code raises `ValueError("User not found")`.
  It is deliberately an allowed READ dispatch and a negative control: no A1/A2
  provider retry and no mutation.

Provider profiles R1/R2/R3 are injected at the loopback endpoint. R4 places
three 503 responses before a WRITE; S1 places three 503 responses after a
successful WRITE and is measured only for no automatic replay/duplicate
mutation. FI-v1 does not add retry of native tools, rollback, repair, or A3.

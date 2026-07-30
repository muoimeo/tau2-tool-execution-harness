# ARC-01 — Native A0 execution path in tau2-bench v0.1.3

## Frozen code identity

- Git tag: `v0.1.3`
- Commit: `5ba9e3e56db57c5e4114bf7f901291f09b2c5619`
- Scope: Retail, native `llm_agent`, no execution harness.

## End-to-end path

```text
tau2 run
  │
  ▼
run_domain(config)
  │ loads tasks; calls run_tasks
  ▼
run_tasks(...)
  │ expands trial × task, assigns one seed per trial, checkpoints completed runs
  ▼
run_task(...)
  │ creates Retail Environment, LLMAgent, UserSimulator, Orchestrator
  ▼
Orchestrator.run() ──► Orchestrator.step()
  │
  ├─ agent.generate_next_message(...)
  │    └─ llm_utils.generate(...)
  │         ├─ Tool.openai_schema → LiteLLM completion(tools=...)
  │         ├─ response.message.tool_calls
  │         ├─ json.loads(function.arguments)
  │         └─ ToolCall → AssistantMessage
  │
  ├─ AssistantMessage.validate()
  │
  └─ for each ToolCall:
       Environment.get_response(tool_call)
         ├─ make_tool_call(name, requestor, **arguments)
         ├─ ToolKitBase.use_tool(name, **kwargs)
         ├─ bound RetailTools function(**kwargs)
         ├─ catches every Exception as "Error: {e}"
         └─ ToolMessage(content, error)
  │
  ▼
SimulationRun(messages, termination, duration)
  │
  ▼
evaluate_simulation(..., EvaluationType.ALL)
  │ DB + ACTION diagnostics + COMMUNICATE; multiplies only task reward_basis
  ▼
run_tasks._save() → results.json
```

## Code-grounded stage map

| Stage | File and symbol | Native input → output | Native validation / exception boundary |
|---|---|---|---|
| Retail environment and tools | `src/tau2/domains/retail/environment.py:16-31`, `get_environment`; `src/tau2/domains/retail/tools.py:21`, `RetailTools` | `RetailDB` → `Environment(tools=RetailTools(db))` | Retail DB is freshly loaded at environment construction when no DB is supplied (`environment.py:22-24`). |
| Tool registration | `src/tau2/environment/toolkit.py:17-38`, `ToolKitType`; `:50-62`, `is_tool`; `:65-92`, `ToolKitBase` | Decorated bound methods → tool-name dictionary and `Tool` objects | Registration is metaclass/decorator based. Duplicate names inside one class dictionary are not separately detected. |
| Schema generation | `src/tau2/environment/tool.py:61-136`, `Tool.__init__`/`parse_data`; `:140-149`, `openai_schema` | Python signature, annotations and docstring → dynamic Pydantic params model → OpenAI function schema | This builds schema; it does not validate model arguments before native dispatch. |
| Schema exposure | `src/tau2/run.py:400-411`, `run_task`; `src/tau2/agent/llm_agent.py:97-115`, `LLMAgent.generate_next_message`; `src/tau2/utils/llm_utils.py:180-215`, `generate` | `environment.get_tools()` → agent → `[tool.openai_schema]` → LiteLLM `completion` | LiteLLM receives schemas at `llm_utils.py:205-215`. API/model exceptions are logged and re-raised at `:208-218`. |
| Model response parsing | `src/tau2/utils/llm_utils.py:219-252`, `generate` | LiteLLM first choice → `AssistantMessage` | Requires assistant role (`:229-231`); parses each arguments string with `json.loads` (`:233-241`). Parse/Pydantic errors escape `generate`; no structured tool error is created. Raw choice is attached only after successful parse at `:244-250`. |
| Message contract check | `src/tau2/data_model/message.py:49-60`, `ToolCall`; `:111-134`, `ParticipantMessageBase.validate`; `src/tau2/orchestrator/orchestrator.py:319-330`, `step` | `AssistantMessage` → route to user or environment | `ToolCall.arguments` must already be a dict by Pydantic. `validate()` only requires nonblank content or non-`None` tool calls. Empty model response raises `ValueError` at `message.py:115-118` and escapes the episode. |
| Dispatch loop | `src/tau2/orchestrator/orchestrator.py:333-355`, `Orchestrator.step` | Each parsed `ToolCall` → `Environment.get_response` | Direct call at `:338-340`; there is no separate harness decision, validator or safe dispatcher. An exception escaping `get_response` aborts the episode. |
| Requestor/name resolution | `src/tau2/environment/environment.py:128-155`, `make_tool_call`; `src/tau2/environment/toolkit.py:76-80`, `use_tool` | `requestor`, name, kwargs → assistant/user toolkit bound function | Invalid requestor/name raises `ValueError`. Because dispatch is called through `get_response`, these are normally collapsed into native `ToolMessage.error=true`. |
| Native function | `src/tau2/environment/tool.py:179-182`, `Tool._call`; `src/tau2/environment/toolkit.py:76-80`, `ToolKitBase.use_tool`; `src/tau2/domains/retail/tools.py` | `**arguments` → bound Retail tool → DB read/write and result | No `params.model_validate(arguments)` call exists on this route. Python catches missing/extra args as `TypeError`; annotations are not runtime type enforcement. Retail tools raise `ValueError` for domain preconditions, e.g. unknown order at `retail/tools.py:41-42`. |
| Result/error wrapping | `src/tau2/environment/environment.py:338`, `to_json_str`; `:390-415`, `get_response` | Result or exception → `ToolMessage` | `get_response` catches every `Exception` around dispatch and `sync_tools` (`:398-406`) and loses exception type, traceback and stage. Serialization occurs after that catch at `:408`, so serialization failure can escape and abort the episode. |
| Trajectory / error limit | `src/tau2/orchestrator/orchestrator.py:244-282`, `run`; `:284-361`, `step`; `:439` error counting | Messages → `SimulationRun` | `max_steps` and `max_errors` are checked after each step (`:254-261`). Tool errors are represented only by `ToolMessage.error`. |
| Batch exception boundary | `src/tau2/run.py:310-348`, `_run`/executor | Episode result → checkpoint; exception → batch exception | `_run` logs then re-raises (`:332-334`); `executor.map` propagates it (`:347-348`). A failed episode is not checkpointed. |
| Evaluation | `src/tau2/run.py:464-474`, `run_task`; `src/tau2/evaluator/evaluator.py:21-139`, `evaluate_simulation` | Completed `SimulationRun` + frozen task → `RewardInfo` | `MAX_STEPS`/`TOO_MANY_ERRORS` short-circuit to reward 0. Under `ALL`, DB/action/communication are all calculated, but only fields listed in task `reward_basis` multiply reward (`evaluator.py:90-130`). |
| Serialization | `src/tau2/run.py:300-308`, `_save`; `src/tau2/data_model/simulation.py:310-354`, `SimulationRun`/`Results` | Pydantic model dump → append in `results.json` | Checkpoint append is lock-protected, but writes the full JSON file in place; there is no atomic temp-file rename. |

## Native exception boundaries

1. **Model/API layer:** LiteLLM gets `num_retries=3` by default
   (`src/tau2/config.py:29`, `llm_utils.py:199-200`). Exhausted API exceptions
   are re-raised. This is model/API retry, not execution retry.
2. **Parser layer:** role assertion, malformed JSON and Pydantic construction
   failures escape before a valid `AssistantMessage` is returned.
3. **Message layer:** empty assistant content plus no tool calls raises in
   `AssistantMessage.validate`; this is outside native `max_errors`.
4. **Tool execution layer:** `Environment.get_response` catches broad
   `Exception` and emits `ToolMessage(error=true, content="Error: ...")`.
   Native output does not preserve stage, class, retryability or cause.
5. **Serialization after execution:** `to_json_str` is outside the broad
   dispatch `try`; its exception escapes.
6. **Batch layer:** any escaped episode exception is re-raised by `_run` and
   stops the executor iteration. Resume logic later skips only checkpointed
   `(trial, task_id, seed)` tuples.

## Native A0 limitations relevant to A1

- Exposed JSON schema and runtime dispatch are disconnected.
- Tool-name validation occurs only inside the dispatcher.
- Requestor routing and name resolution are not represented as distinct stages.
- Domain preconditions, Python argument errors, tool bugs and `sync_tools`
  failures all become the same native error shape.
- No immutable pre-dispatch request record, validator decision, exception type,
  per-call latency or state boundary is persisted in native `results.json`.
- `raw_data` is stored only after successful parsing, so malformed raw tool-call
  responses cannot be reconstructed from A0 artifacts.


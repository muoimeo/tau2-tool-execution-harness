"""Gate 1: production run path with only LiteLLM completion mocked."""
import json
from types import SimpleNamespace
import pytest
import hashlib
from functools import wraps

from tau2.run import load_tasks, run_tasks
import tau2.run as run_module
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.utils import llm_utils
from tau2.domains.mock.tools import MockTools
from tau2.harness.dispatcher import NativeToolExecutor
from tau2.harness.integration_contracts import EpisodeLifecycleStore, LifecycleStatus
from tau2.harness.retry import ProviderFailureKind, ProviderRetrySidecar, RetryAttempt
from tau2.utils.display import ConsoleDisplay

class Http503(Exception):
    status_code = 503

def _response(content):
    choice = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(role="assistant", content=content, tool_calls=[]))
    choice.to_dict = lambda: {}
    return SimpleNamespace(choices=[choice], model="mock", to_dict=lambda: {})

def _tool_response():
    call = SimpleNamespace(id="call-1", function=SimpleNamespace(
        name="create_task",
        arguments='{"user_id":"user_1","title":"Important Meeting"}',
    ))
    return _response(None) if False else SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(role="assistant", content=None, tool_calls=[call]), to_dict=lambda: {})], model="mock", to_dict=lambda: {})

def test_a2_exhaustion_uses_production_run_path_and_writes_no_result(tmp_path, monkeypatch):
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        # User simulator receives the native first provider response. Agent calls
        # are identified by tools and exhaust the A2-owned retry budget.
        if kwargs["model"] == "user": return _response("please help")
        raise Http503()
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")
    task = load_tasks("mock")[0]
    save = tmp_path / "results.json"; trace = tmp_path / "traces"
    run_tasks(domain="mock", tasks=[task], agent="llm_agent", user="user_simulator", llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={}, num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False, max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace, a2_retry=True)
    agent_calls = [item for item in calls if item["model"] == "agent"]
    assert len(agent_calls) == 3 and all(item["num_retries"] == 0 for item in agent_calls)
    result = json.loads(save.read_text()); assert result["simulations"] == []
    lifecycle = next(trace.glob("*.lifecycle.json")); value = json.loads(lifecycle.read_text())
    assert value["status"] == "failed_operational" and value["provider_retry_event_count"] == 3 and value["provider_retry_last_sequence"] == 3
    sidecar = trace / value["provider_retry_filename"]; rows = [json.loads(x) for x in sidecar.read_text().splitlines()]
    assert [x["sequence"] for x in rows] == [1,2,3]
    assert len({x["generation_id"] for x in rows}) == 1
    assert [x["provider_attempt"] for x in rows] == [1,2,3]
    assert [x["backoff_seconds"] for x in rows] == [1.0,2.0,0.0]
    assert not list(trace.glob("*attempt-002*"))
    assert all("dispatch_started" not in line for path in trace.glob("*.jsonl") if "provider-retry" not in path.name for line in path.read_text().splitlines())

def test_a2_resume_after_exhaustion_preserves_failed_attempt_and_serializes_once(tmp_path, monkeypatch):
    calls, agent_calls, user_calls = [], [0], [0]
    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 3 else "please help")
        agent_calls[0] += 1
        if agent_calls[0] <= 3: raise Http503()
        return _tool_response()
    monkeypatch.setattr(llm_utils, "completion", completion); monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0); monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    from tau2.utils.display import ConsoleDisplay
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")
    task = load_tasks("mock")[0]; save=tmp_path/"results.json"; trace=tmp_path/"traces"
    common=dict(domain="mock", tasks=[task], agent="llm_agent", user="user_simulator", llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={}, num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False, max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace, a2_retry=True)
    run_tasks(**common)
    old_lifecycle=next(trace.glob("*attempt-001.lifecycle.json")); old_sidecar=next(trace.glob("*attempt-001.provider-retry.jsonl")); before_lifecycle=old_lifecycle.read_bytes(); before_sidecar=old_sidecar.read_bytes(); before_sha=hashlib.sha256(before_sidecar).hexdigest(); before_result=save.read_bytes()
    old=json.loads(before_lifecycle); old_rows=[json.loads(x) for x in before_sidecar.splitlines()]
    assert old["status"] == "failed_operational"
    assert old["provider_retry_event_count"] == 3 and old["provider_retry_last_sequence"] == 3
    assert [row["sequence"] for row in old_rows] == [1, 2, 3]
    assert [row["provider_attempt"] for row in old_rows] == [1, 2, 3]
    assert json.loads(before_result)["simulations"] == []
    assert not list(trace.glob("*attempt-002*"))
    run_tasks(**common)
    assert old_lifecycle.read_bytes()==before_lifecycle and old_sidecar.read_bytes()==before_sidecar and hashlib.sha256(old_sidecar.read_bytes()).hexdigest()==before_sha
    assert json.loads(old_lifecycle.read_text()) == old
    new_lifecycle=next(trace.glob("*attempt-002.lifecycle.json")); new=json.loads(new_lifecycle.read_text()); new_sidecar=trace/new["provider_retry_filename"]
    rows=[json.loads(x) for x in new_sidecar.read_text().splitlines()]
    # This scripted episode has two successful agent generations (one tool call
    # and one post-tool response).  A2 ownership is per generation, so both
    # begin at provider_attempt=1 and neither is retried.
    assert new["status"]=="closed" and [x["sequence"] for x in rows]==[1,2]
    assert [x["provider_attempt"] for x in rows]==[1,1]
    assert all("attempt-002" in row["generation_id"] for row in rows)
    assert len({row["generation_id"] for row in rows}) == 2
    assert new["provider_retry_event_count"] == 2
    assert new["provider_retry_last_sequence"] == 2
    assert new["provider_retry_sha256"] == hashlib.sha256(new_sidecar.read_bytes()).hexdigest()
    assert agent_calls[0] == 5
    assert all(item["num_retries"] == 0 for item in calls if item["model"] == "agent")
    result=json.loads(save.read_text())["simulations"]; assert len(result)==1 and len({(x["task_id"],x["trial"]) for x in result})==1

def test_a2_production_resume_rejects_corrupt_failed_sidecar_before_attempt_two(tmp_path, monkeypatch):
    # Gate-1 helper produces the failed production attempt; reuse its fault shape.
    calls=[]
    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"]=="user": return _response("please help")
        raise Http503()
    monkeypatch.setattr(llm_utils,"completion",completion); monkeypatch.setattr(llm_utils,"get_response_cost",lambda _:0.0); monkeypatch.setattr(llm_utils,"get_response_usage",lambda _:None)
    from tau2.utils.display import ConsoleDisplay
    monkeypatch.setattr(ConsoleDisplay.console,"input",lambda *_:"y")
    task=load_tasks("mock")[0]; save=tmp_path/"results.json"; trace=tmp_path/"traces"; common=dict(domain="mock",tasks=[task],agent="llm_agent",user="user_simulator",llm_agent="agent",llm_args_agent={},llm_user="user",llm_args_user={},num_trials=1,max_steps=5,max_errors=2,save_to=save,console_display=False,max_concurrency=1,seed=300,harness_enabled=True,harness_trace_root=trace,a2_retry=True)
    run_tasks(**common)
    sidecar=next(trace.glob("*attempt-001.provider-retry.jsonl")); sidecar.write_text("bad\n"); before=len(calls)
    with pytest.raises(Exception): run_tasks(**common)
    assert len(calls)==before and not list(trace.glob("*attempt-002*")) and json.loads(save.read_text())["simulations"]==[]


def test_a2_retry_then_valid_write_dispatches_and_mutates_exactly_once(tmp_path, monkeypatch):
    """A provider retry precedes one accepted WRITE; the WRITE is never replayed."""
    provider_calls, user_calls = [], [0]
    counts = {"dispatcher": 0, "write": 0, "mutations": 0}

    def completion(**kwargs):
        provider_calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 2 else "please help")
        agent_calls = [call for call in provider_calls if call["model"] == "agent"]
        if len(agent_calls) == 1:
            raise Http503()
        if len(agent_calls) == 2:
            return _tool_response()
        return _response("The task has been created.")

    original_execute_once = NativeToolExecutor.execute_once

    @wraps(original_execute_once)
    @wraps(original_execute_once)
    def counted_execute_once(self, request):
        counts["dispatcher"] += 1
        return original_execute_once(self, request)

    original_create_task = MockTools.create_task

    @wraps(original_create_task)
    @wraps(original_create_task)
    def counted_create_task(self, *args, **kwargs):
        before = (len(self.db.tasks), tuple(self.db.users["user_1"].tasks))
        value = original_create_task(self, *args, **kwargs)
        after = (len(self.db.tasks), tuple(self.db.users["user_1"].tasks))
        counts["write"] += 1
        counts["mutations"] += int(after != before)
        return value

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(NativeToolExecutor, "execute_once", counted_execute_once)
    monkeypatch.setattr(MockTools, "create_task", counted_create_task)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")

    task = load_tasks("mock")[0]
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    run_tasks(
        domain="mock", tasks=[task], agent="llm_agent", user="user_simulator",
        llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
        num_trials=1, max_steps=5, max_errors=2, save_to=save,
        console_display=False, max_concurrency=1, seed=300,
        harness_enabled=True, harness_trace_root=trace, a2_retry=True,
    )

    agent_calls = [call for call in provider_calls if call["model"] == "agent"]
    assert len(agent_calls) == 3
    assert all(call["num_retries"] == 0 for call in agent_calls)
    lifecycle = json.loads(next(trace.glob("*.lifecycle.json")).read_text())
    sidecar = trace / lifecycle["provider_retry_filename"]
    provider_rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    write_generation = [
        row for row in provider_rows
        if row["generation_id"] == provider_rows[0]["generation_id"]
    ]
    assert [row["provider_attempt"] for row in write_generation] == [1, 2]
    assert [row["outcome"] for row in write_generation] == ["retry_scheduled", "success"]
    assert len({row["generation_id"] for row in provider_rows}) == 2
    assert provider_rows[-1]["provider_attempt"] == 1
    assert provider_rows[-1]["outcome"] == "success"

    assert counts == {"dispatcher": 1, "write": 1, "mutations": 1}
    tool_trace = trace / lifecycle["trace_filename"]
    tool_rows = [json.loads(line) for line in tool_trace.read_text().splitlines()]
    assert [row["phase"] for row in tool_rows] == [
        "proposed", "parsed", "validated", "dispatch_started",
        "dispatch_completed", "result_emitted",
    ]
    assert sum(row["phase"] == "result_emitted" for row in tool_rows) == 1
    assert lifecycle["status"] == "closed"
    assert lifecycle["provider_retry_event_count"] == 3
    assert lifecycle["provider_retry_last_sequence"] == 3
    assert lifecycle["provider_retry_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert lifecycle["trace_event_count"] == 6
    assert lifecycle["trace_last_sequence"] == 5
    assert lifecycle["trace_sha256"] == hashlib.sha256(tool_trace.read_bytes()).hexdigest()
    assert not list(trace.glob("*attempt-002*"))
    simulations = json.loads(save.read_text())["simulations"]
    assert len(simulations) == 1
    assert len({(item["task_id"], item["trial"]) for item in simulations}) == 1
    assert sum(message.get("role") == "tool" for message in simulations[0]["messages"]) == 1


def test_console_display_unicode_failure_is_nonfatal_after_successful_write(tmp_path, monkeypatch):
    """Display is post-commit and cannot turn a valid WRITE into an operational failure."""
    calls, user_calls, warnings = [], [0], []
    counts = {"dispatcher": 0, "write": 0, "mutations": 0}

    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 2 else "please help")
        agent_calls = [call for call in calls if call["model"] == "agent"]
        return _tool_response() if len(agent_calls) == 1 else _response("Created.")

    original_execute_once = NativeToolExecutor.execute_once

    @wraps(original_execute_once)
    def counted_execute_once(self, request):
        counts["dispatcher"] += 1
        return original_execute_once(self, request)

    original_create_task = MockTools.create_task

    @wraps(original_create_task)
    def counted_create_task(self, *args, **kwargs):
        before = (len(self.db.tasks), tuple(self.db.users["user_1"].tasks))
        value = original_create_task(self, *args, **kwargs)
        after = (len(self.db.tasks), tuple(self.db.users["user_1"].tasks))
        counts["write"] += 1
        counts["mutations"] += int(after != before)
        return value

    def broken_display(cls, *_args, **_kwargs):
        raise UnicodeEncodeError("cp1252", "✅", 0, 1, "not encodable")

    import tau2.utils.display as display_module
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(NativeToolExecutor, "execute_once", counted_execute_once)
    monkeypatch.setattr(MockTools, "create_task", counted_create_task)
    monkeypatch.setattr(ConsoleDisplay, "display_simulation", classmethod(broken_display))
    monkeypatch.setattr(display_module.logger, "warning", lambda message: warnings.append(message))

    save, trace = tmp_path / "results.json", tmp_path / "traces"
    run_tasks(
        domain="mock", tasks=[load_tasks("mock")[0]], agent="llm_agent",
        user="user_simulator", llm_agent="agent", llm_args_agent={},
        llm_user="user", llm_args_user={}, num_trials=1, max_steps=5,
        max_errors=2, save_to=save, console_display=True, max_concurrency=1,
        seed=300, harness_enabled=True, harness_trace_root=trace, a2_retry=True,
    )

    lifecycle = json.loads(next(trace.glob("*.lifecycle.json")).read_text())
    sidecar = trace / lifecycle["provider_retry_filename"]
    tool_trace = trace / lifecycle["trace_filename"]
    provider_rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    tool_rows = [json.loads(line) for line in tool_trace.read_text().splitlines()]
    simulations = json.loads(save.read_text())["simulations"]

    assert warnings and "Non-critical simulation display failed" in warnings[0]
    assert counts == {"dispatcher": 1, "write": 1, "mutations": 1}
    assert len(simulations) == 1
    assert len({(item["task_id"], item["trial"]) for item in simulations}) == 1
    assert lifecycle["status"] == "closed"
    assert lifecycle["provider_retry_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert lifecycle["provider_retry_event_count"] == len(provider_rows)
    assert [row["sequence"] for row in provider_rows] == list(range(1, len(provider_rows) + 1))
    assert lifecycle["trace_sha256"] == hashlib.sha256(tool_trace.read_bytes()).hexdigest()
    assert lifecycle["trace_event_count"] == len(tool_rows)
    assert [row["sequence"] for row in tool_rows] == list(range(len(tool_rows)))


def test_console_display_uses_ascii_fallback_after_cp1252_unicode_error(monkeypatch):
    printed, warnings = [], []

    class Cp1252Console:
        def print(self, value, **kwargs):
            text = str(value)
            text.encode("cp1252")
            printed.append((text, kwargs))

    def broken_display(cls, *_args, **_kwargs):
        raise UnicodeEncodeError("cp1252", "✅", 0, 1, "not encodable")

    import tau2.utils.display as display_module
    monkeypatch.setattr(ConsoleDisplay, "console", Cp1252Console())
    monkeypatch.setattr(ConsoleDisplay, "display_simulation", classmethod(broken_display))
    monkeypatch.setattr(display_module.logger, "warning", lambda message: warnings.append(message))

    assert ConsoleDisplay.display_simulation_best_effort(object(), show_details=False) is False
    assert printed and printed[0][0].isascii()
    assert warnings and "Non-critical simulation display failed" in warnings[0]


def test_a2_safe_exhaustion_continues_and_serializes_only_later_success(tmp_path, monkeypatch):
    """A pre-dispatch A2 exhaustion is operational evidence, not a fake reward row."""
    calls, user_calls = [], [0]
    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 3 else "please help")
        agent_calls = [item for item in calls if item["model"] == "agent"]
        if len(agent_calls) <= 3:
            raise Http503()
        return _tool_response() if len(agent_calls) == 4 else _response("Created.")
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)

    first = load_tasks("mock")[0]
    second = first.model_copy(update={"id": "create_task_2"})
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    run_tasks(domain="mock", tasks=[first, second], agent="llm_agent", user="user_simulator",
              llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
              num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
              max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
              a2_retry=True, a2_max_operational_failures=1)

    rows = json.loads(save.read_text())["simulations"]
    assert [row["task_id"] for row in rows] == ["create_task_2"]
    lifecycles = {json.loads(path.read_text())["identity"]["task_id"]: json.loads(path.read_text()) for path in trace.glob("*.lifecycle.json")}
    assert lifecycles["create_task_1"]["status"] == "failed_operational"
    assert lifecycles["create_task_1"]["side_effect_may_have_occurred"] is False
    assert lifecycles["create_task_2"]["status"] == "closed"
    assert len([call for call in calls if call["model"] == "agent"]) == 5


def test_a2_safe_operational_failure_budget_stops_only_after_exceeded(tmp_path, monkeypatch):
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return _response("please help") if kwargs["model"] == "user" else (_ for _ in ()).throw(Http503())
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    base = load_tasks("mock")[0]
    tasks = [base.model_copy(update={"id": f"safe_{index}"}) for index in range(3)]
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    with pytest.raises(Exception, match="operational-failure budget exceeded"):
        run_tasks(domain="mock", tasks=tasks, agent="llm_agent", user="user_simulator",
                  llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
                  num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
                  max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
                  a2_retry=True, a2_max_operational_failures=2)
    assert len([call for call in calls if call["model"] == "agent"]) == 9
    assert json.loads(save.read_text())["simulations"] == []
    assert len(list(trace.glob("*.lifecycle.json"))) == 3


def test_a2_safe_operational_failure_budget_persists_across_run_resumes(tmp_path, monkeypatch):
    """A wrapper restart must not reset the A2 safe-failure budget."""
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response("please help") if kwargs["model"] == "user" else (_ for _ in ()).throw(Http503())

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")

    task = load_tasks("mock")[0]
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    common = dict(
        domain="mock", tasks=[task], agent="llm_agent", user="user_simulator",
        llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
        num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
        max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
        a2_retry=True, a2_max_operational_failures=1,
    )

    # First invocation records one safe operational failure and may return.
    run_tasks(**common)
    assert len([call for call in calls if call["model"] == "agent"]) == 3
    assert len(list(trace.glob("*.lifecycle.json"))) == 1

    # A second process-equivalent invocation records failure #2 and exceeds
    # the same frozen budget rather than resetting it to zero.
    with pytest.raises(Exception, match="operational-failure budget exceeded"):
        run_tasks(**common)
    assert len([call for call in calls if call["model"] == "agent"]) == 6
    assert len(list(trace.glob("*.lifecycle.json"))) == 2

    # A later resume sees the persisted excess and fails before Environment /
    # provider work, so no attempt-003 can be created.
    with pytest.raises(Exception, match="budget already exceeded across"):
        run_tasks(**common)
    assert len([call for call in calls if call["model"] == "agent"]) == 6
    assert len(list(trace.glob("*.lifecycle.json"))) == 2


def test_a2_skip_exhausted_unsafe_episode_is_exact_task_trial_and_keeps_siblings(tmp_path, monkeypatch):
    """An exhausted 78:0-like episode is accounted for without hiding siblings."""
    calls = []

    def fake_run_task(**kwargs):
        calls.append((str(kwargs["task"].id), kwargs["trial_index"]))
        return SimulationRun(
            id=f"{kwargs['task'].id}-{kwargs['trial_index']}",
            task_id=str(kwargs["task"].id),
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:00:01",
            duration=1.0,
            termination_reason=TerminationReason.AGENT_STOP,
            messages=[],
            seed=kwargs["seed"],
        )

    monkeypatch.setattr(run_module, "run_task", fake_run_task)
    monkeypatch.setattr(run_module, "validate_a2_episode_before_results_commit", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_module, "finalize_episode_after_results_commit", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_module, "validate_closed_episode_skip", lambda *args, **kwargs: True)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")

    base = load_tasks("mock")[0]
    skip_task = base.model_copy(update={"id": "skip_1"})
    sibling = base.model_copy(update={"id": "sibling"})
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    common = dict(
        domain="mock", tasks=[skip_task, sibling], agent="llm_agent", user="user_simulator",
        llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
        num_trials=2, max_steps=5, max_errors=2, save_to=save, console_display=False,
        max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
        a2_retry=True, a2_max_operational_failures=3, a2_max_unsafe_manual_resumes=2,
    )

    # Seed a prior native-results file with all tuples, then remove skip_1:0,
    # skip_1:1 and sibling:1 to prove the task's sibling trial still runs.
    run_tasks(**common)
    document = json.loads(save.read_text())
    document["simulations"] = [
        row for row in document["simulations"]
        if (row["task_id"], row["trial"]) not in {
            ("skip_1", 0), ("skip_1", 1), ("sibling", 1)
        }
    ]
    save.write_text(json.dumps(document, indent=2), encoding="utf-8")

    import random
    random.seed(300)
    trial_seeds = [random.randint(0, 1000000) for _ in range(2)]
    from tau2.harness.entrypoint import A1RunSettings
    settings = A1RunSettings.create(
        trace_root=trace, domain="mock", task_ids=["skip_1", "sibling"],
        agent="llm_agent", user="user_simulator", llm_agent="agent", llm_user="user",
        num_trials=2, trial_seeds=trial_seeds, max_steps=5, max_errors=2,
        max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={},
        a2_retry=True, a2_max_operational_failures=3, a2_max_unsafe_manual_resumes=2,
    )
    identity = settings.identity(task_id="skip_1", trial_index=0, trial_seed=trial_seeds[0])
    store = EpisodeLifecycleStore(trace, identity, max_unsafe_manual_resumes=2)
    opened = store.open()
    store.trace_path.write_text(json.dumps({
        "sequence": 0, "phase": "dispatch_completed", "native_tool_type": "write",
        "tool_name": "modify_pending_order_address",
    }) + "\n", encoding="utf-8")
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id="g1", turn_index=1, episode_attempt=1)
    for attempt in range(1, 4):
        sidecar.record(RetryAttempt(
            attempt, ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE, attempt < 3,
            1.0 if attempt == 1 else 2.0 if attempt == 2 else 0.0,
        ))
    store.terminalize(opened, status=LifecycleStatus.FAILED_OPERATIONAL, outcome="failed", error="exhausted", side_effect=True)
    (trace / "manual-unsafe-resume-audit.jsonl").write_text(
        "\n".join(json.dumps({
            "authorization": f"skip_1:0:{attempt}", "task_id": "skip_1", "trial_index": 0,
            "failed_attempt": attempt, "outcome": "authorized_new_attempt",
        }) for attempt in (1, 2)) + "\n", encoding="utf-8"
    )

    calls.clear()
    run_tasks(**common, a2_skip_unresolved=["skip_1:0"])
    assert calls == [("skip_1", 1), ("sibling", 1)]
    rows = json.loads(save.read_text())["simulations"]
    assert {(row["task_id"], row["trial"]) for row in rows} == {
        ("skip_1", 1), ("sibling", 0), ("sibling", 1)
    }
    assert not any(row["task_id"] == "skip_1" and row["trial"] == 0 for row in rows)
    accounting = json.loads((trace / "a2-operational-accounting.json").read_text())
    assert accounting["planned_logical_episodes"] == 4
    assert accounting["benchmark_completed"] == 3
    assert accounting["operationally_unresolved"] == [{
        "task_id": "skip_1", "trial": 0, "seed": trial_seeds[0],
        "latest_attempt": 1, "reason": "unsafe_resume_budget_exhausted",
    }]


def test_a2_exhaustion_after_write_fails_fast_without_replaying_write(tmp_path, monkeypatch):
    calls, user_calls = [], [0]
    counts = {"dispatcher": 0, "write": 0}
    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("###STOP###" if user_calls[0] == 2 else "please help")
        agent_calls = [item for item in calls if item["model"] == "agent"]
        if len(agent_calls) == 1:
            return _tool_response()
        raise Http503()
    original_execute_once, original_create_task = NativeToolExecutor.execute_once, MockTools.create_task
    @wraps(original_execute_once)
    def counted_execute_once(self, request):
        counts["dispatcher"] += 1
        return original_execute_once(self, request)
    @wraps(original_create_task)
    def counted_create_task(self, *args, **kwargs):
        counts["write"] += 1
        return original_create_task(self, *args, **kwargs)
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(NativeToolExecutor, "execute_once", counted_execute_once)
    monkeypatch.setattr(MockTools, "create_task", counted_create_task)
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    with pytest.raises(Exception):
        run_tasks(domain="mock", tasks=[load_tasks("mock")[0]], agent="llm_agent", user="user_simulator",
                  llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
                  num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
                  max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
                  a2_retry=True, a2_max_operational_failures=3)
    lifecycle = json.loads(next(trace.glob("*.lifecycle.json")).read_text())
    assert lifecycle["status"] == "failed_operational"
    assert lifecycle["side_effect_may_have_occurred"] is True
    assert counts == {"dispatcher": 1, "write": 1}
    assert json.loads(save.read_text())["simulations"] == []


def test_a2_manual_authorized_unsafe_resume_creates_fresh_attempt_once(tmp_path, monkeypatch):
    calls, user_calls, db_ids = [], [0], []
    counts = {"dispatcher": 0, "write": 0}
    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "user":
            user_calls[0] += 1
            return _response("please help")
        agent_calls = [item for item in calls if item["model"] == "agent"]
        if len(agent_calls) in (1, 5):
            return _tool_response()
        if len(agent_calls) in (2, 3, 4):
            raise Http503()
        return _response("Updated.")
    original_execute_once, original_create_task = NativeToolExecutor.execute_once, MockTools.create_task
    @wraps(original_execute_once)
    def counted_execute_once(self, request):
        counts["dispatcher"] += 1
        return original_execute_once(self, request)
    @wraps(original_create_task)
    def counted_create_task(self, *args, **kwargs):
        db_ids.append(id(self.db))
        counts["write"] += 1
        return original_create_task(self, *args, **kwargs)
    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    monkeypatch.setattr(NativeToolExecutor, "execute_once", counted_execute_once)
    monkeypatch.setattr(MockTools, "create_task", counted_create_task)
    monkeypatch.setattr(ConsoleDisplay.console, "input", lambda *_: "y")
    task = load_tasks("mock")[0]
    save, trace = tmp_path / "results.json", tmp_path / "traces"
    common = dict(domain="mock", tasks=[task], agent="llm_agent", user="user_simulator",
                  llm_agent="agent", llm_args_agent={}, llm_user="user", llm_args_user={},
                  num_trials=1, max_steps=5, max_errors=2, save_to=save, console_display=False,
                  max_concurrency=1, seed=300, harness_enabled=True, harness_trace_root=trace,
                  a2_retry=True, a2_max_operational_failures=3)
    with pytest.raises(Exception):
        run_tasks(**common)
    old_lifecycle = next(trace.glob("*attempt-001.lifecycle.json"))
    old_sidecar = next(trace.glob("*attempt-001.provider-retry.jsonl"))
    lifecycle_bytes, sidecar_bytes = old_lifecycle.read_bytes(), old_sidecar.read_bytes()
    with pytest.raises(Exception, match="manual resume authorization"):
        run_tasks(**common)
    assert not list(trace.glob("*attempt-002.lifecycle.json"))
    run_tasks(**common, a2_authorize_unsafe_resume=["create_task_1:0:1"])
    assert old_lifecycle.read_bytes() == lifecycle_bytes
    assert old_sidecar.read_bytes() == sidecar_bytes
    audit = trace / "manual-unsafe-resume-audit.jsonl"
    assert audit.exists() and "create_task_1:0:1" in audit.read_text()
    assert (trace / next(path.name for path in trace.glob("*attempt-002.lifecycle.json"))).exists()
    assert counts == {"dispatcher": 2, "write": 2}
    assert len(set(db_ids)) == 2
    rows = json.loads(save.read_text())["simulations"]
    assert len(rows) == 1 and len({(row["task_id"], row["trial"]) for row in rows}) == 1
    with pytest.raises(Exception, match="already used|target attempt already exists"):
        run_tasks(**common, a2_authorize_unsafe_resume=["create_task_1:0:1"])

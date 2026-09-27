"""Explicit runner for FI-v1; it is never invoked by ``tau2 run`` itself."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from tau2.fault_injection.contracts import CompiledFixture, FaultKind, FaultScenario, ScenarioObservation
from tau2.fault_injection.injector import A3NativeResultFaultInjector, apply_fault
from tau2.fault_injection.observer import (
    NativeExecutionObserver,
    summarize_harness_trace,
    summarize_provider_recovery,
)
from tau2.fault_injection.scripted_provider import LocalScriptedProvider
from tau2.run import get_tasks, run_tasks


class FIRunnerError(RuntimeError):
    pass


TrackName = Literal["a0-native", "a1-validation", "a2-retry", "a3-recovery"]


def scenario_directory(root: str | Path, scenario: FaultScenario) -> Path:
    return Path(root) / scenario.track / scenario.scenario_id


def write_scenario_plan(root: str | Path, scenario: FaultScenario, fixture: CompiledFixture) -> Path:
    """Create a frozen plan before running a scenario; never overwrite it."""
    destination = scenario_directory(root, scenario)
    if destination.exists():
        raise FileExistsError(f"FI scenario artifact directory already exists: {destination}")
    destination.mkdir(parents=True)
    plan = {
        "schema_version": "fi-v1-plan-1",
        "evidence_only": True,
        "scenario": scenario.model_dump(mode="json"),
        "fixture_id": fixture.fixture_id,
        "fixture_sha256": fixture.fixture_sha256,
        "source_results_sha256": fixture.source_results_sha256,
        "provider_transport": "local_openai_compatible_loopback",
        "gateway_called": False,
        "evaluator_mode": "in_memory_task_copy_without_evaluation_criteria",
    }
    target = destination / "scenario-plan.json"
    target.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def execute_scenario(
    root: str | Path,
    scenario: FaultScenario,
    fixture: CompiledFixture,
    *,
    max_steps: int = 200,
    max_errors: int = 10,
) -> ScenarioObservation:
    """Run one explicitly planned FI scenario through the production path.

    The local server is the only provider endpoint.  Results are written below
    the FI root, never to an A0/A1/A2 path.  This function is intentionally not
    called by fixture compilation or tests unless a caller explicitly invokes
    it.
    """
    destination = scenario_directory(root, scenario)
    plan_path = destination / "scenario-plan.json"
    if not plan_path.exists():
        raise FIRunnerError("write_scenario_plan must be called before execution")
    if (destination / "results.json").exists():
        raise FIRunnerError("refusing to overwrite an FI scenario result")
    scripted_fixture = apply_fault(fixture, scenario)
    observer = NativeExecutionObserver()
    harness_enabled = scenario.track != "a0-native"
    a2_retry = scenario.track in {"a2-retry", "a3-recovery"}
    a3_recovery = scenario.track == "a3-recovery"
    result_fault = None
    if scenario.fault in {
        FaultKind.A3_INVALID_OUTPUT_AFTER_WRITE,
        FaultKind.A3_POSTCONDITION_FAIL_AFTER_WRITE,
    }:
        target = fixture.agent_actions[scenario.target_ordinal].response or {}
        message = ((target.get("choices") or [{}])[0]).get("message") or {}
        calls = message.get("tool_calls") or []
        if len(calls) != 1:
            raise FIRunnerError("A3 native-result fault needs exactly one target tool call")
        result_fault = A3NativeResultFaultInjector(
            scenario.fault,
            expected_tool=calls[0]["function"]["name"],
        )
    source_tasks = get_tasks("retail", task_ids=[fixture.task_id])
    if len(source_tasks) != 1:
        raise FIRunnerError("FI fixture task is not uniquely available in Retail")
    # Fault cases intentionally diverge from a benchmark task's expected
    # transcript. Use an in-memory copy without evaluation criteria so the FI
    # result answers execution questions only. The Retail task file is never
    # modified and this copy is marked in scenario-plan.json.
    tasks = [source_tasks[0].model_copy(update={"evaluation_criteria": None})]
    safe_requests: list[dict] = []
    server = LocalScriptedProvider(scripted_fixture)
    try:
        with server:
            results = run_tasks(
                domain="retail",
                tasks=tasks,
                agent="llm_agent",
                user="user_simulator",
                llm_agent=fixture.agent_model,
                llm_args_agent={"temperature": 0.0, "api_base": server.api_base, "api_key": "fi-local-key", "timeout": 2},
                llm_user=fixture.user_model,
                llm_args_user={"temperature": 0.0, "api_base": server.api_base, "api_key": "fi-local-key", "timeout": 2},
                num_trials=1,
                max_steps=max_steps,
                max_errors=max_errors,
                save_to=destination / "results.json",
                console_display=False,
                max_concurrency=1,
                seed=300,
                log_level="WARNING",
                harness_enabled=harness_enabled,
                harness_trace_root=(destination / "traces") if harness_enabled else None,
                a2_retry=a2_retry,
                a3_recovery=a3_recovery,
                # FI executes a deliberately small, local scenario. Safe
                # provider failure should be represented as no result, not a
                # fabricated reward, and never continue beyond this scenario.
                a2_max_operational_failures=0,
                tool_execution_observer_factory=(lambda _task, _trial, _seed: observer),
                native_result_fault_injector_factory=(
                    (lambda _task, _trial, _seed: result_fault)
                    if result_fault is not None else None
                ),
            )
    except Exception as exc:
        disposition = f"exception:{type(exc).__name__}"
        results = None
        error = f"{type(exc).__name__}: {exc}"
    else:
        disposition = "serialized" if results.simulations else "failed_operational"
        error = None
    finally:
        safe_requests = [item.__dict__ for item in server.requests]
        safe_server_errors = [item.__dict__ for item in server.errors]
    harness_evidence = (
        summarize_harness_trace(destination / "traces") if harness_enabled else None
    )
    provider_recovery = (
        summarize_provider_recovery(destination / "traces") if a2_retry else None
    )
    recovery_audit_rows = []
    if a3_recovery:
        for path in sorted((destination / "traces").glob("*.recovery.jsonl")):
            recovery_audit_rows.extend(
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            )
    evidence = harness_evidence or {
        "dispatch_count": observer.dispatch_count,
        "write_dispatch_count": observer.write_dispatch_count,
        "state_hash_before": observer.calls[0].state_hash_before if observer.calls else None,
        "state_hash_after": observer.calls[-1].state_hash_after if observer.calls else None,
        "mutation_count": observer.mutation_count,
        "invariant_violations": [],
    }
    observation = ScenarioObservation(
        scenario_id=scenario.scenario_id,
        disposition=disposition,
        provider_attempt_count=len(safe_requests),
        dispatch_count=evidence["dispatch_count"],
        write_dispatch_count=evidence["write_dispatch_count"],
        state_hash_before=evidence["state_hash_before"],
        state_hash_after=evidence["state_hash_after"],
        recovery_outcome=(
            provider_recovery
            or ("serialized" if results is not None and results.simulations else None)
        ),
        invariant_violations=evidence["invariant_violations"],
    )
    (destination / "scenario-observation.json").write_text(
        json.dumps({
            **observation.model_dump(mode="json"),
            "native_mutation_count": evidence["mutation_count"],
            "native_tool_calls": (
                evidence["native_tool_calls"]
                if harness_evidence is not None
                else [item.__dict__ for item in observer.calls]
            ),
            "provider_request_shapes": safe_requests,
            "provider_server_errors": safe_server_errors,
            "a3_recovery_audit": recovery_audit_rows,
            "a3_native_result_injector_invocations": result_fault.invocations if result_fault else 0,
            "error": error,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return observation

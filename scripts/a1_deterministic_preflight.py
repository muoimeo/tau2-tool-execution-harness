"""Run an EXP-02 local deterministic preflight through ``tau2.cli.main``.

The script substitutes a process-local test registry with scripted components;
it does not contact an LLM provider, gateway, Retail tool, or evaluator.  Its
purpose is to exercise the production CLI -> run_domain -> run_tasks ->
run_task -> Orchestrator A1 binding and persist inspectable artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.simulation import RewardInfo
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.user.base import BaseUser
from tau2.utils.utils import DATA_DIR


class DeterministicToolkit(ToolKitBase):
    calls = 0

    def __init__(self):
        super().__init__(db={"value": 0})

    @is_tool(ToolType.WRITE)
    def increment(self, amount: int) -> int:
        type(self).calls += 1
        self.db["value"] += amount
        return self.db["value"]


class DeterministicAgent(LLMAgent):
    fail = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._turn = 0

    def generate_next_message(self, message, state):
        if type(self).fail:
            raise RuntimeError("deterministic preflight failure before SimulationRun")
        if self._turn == 0:
            self._turn += 1
            if self.harness_parser_enabled:
                self._harness_parser_context_supplier()
            return AssistantMessage(
                role="assistant",
                tool_calls=[ToolCall(id="deterministic-write", name="increment", arguments={"amount": 1})],
            ), state
        return AssistantMessage(role="assistant", content="STOP"), state

    @classmethod
    def is_stop(cls, message):
        return message.content == "STOP"


class DeterministicUser(BaseUser):
    def __init__(self, tools=None, **kwargs):
        super().__init__(**kwargs)

    def get_init_state(self, message_history=None):
        return None

    def generate_next_message(self, message, state):
        return UserMessage(role="user", content="continue"), state

    @classmethod
    def is_stop(cls, message):
        return False


class LocalRegistry:
    def __init__(self, task):
        self._task = task
        self.environment_creations = 0

    def get_info(self):
        return SimpleNamespace(
            domains=["mock"], agents=["llm_agent"], users=["user_simulator"], task_sets=["mock"],
        )

    def get_tasks_loader(self, name):
        if name != "mock":
            raise ValueError(name)
        return lambda: [self._task]

    def get_env_constructor(self, domain):
        if domain != "mock":
            raise ValueError(domain)

        def construct():
            self.environment_creations += 1
            return Environment(domain_name="mock", policy="local deterministic policy", tools=DeterministicToolkit())

        return construct

    def get_agent_constructor(self, name):
        return DeterministicAgent

    def get_user_constructor(self, name):
        return DeterministicUser


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cli_args(*, save_to: str, trace_root: str, task_id: str) -> list[str]:
    return [
        "tau2", "run", "--domain", "mock", "--task-ids", task_id,
        "--agent", "llm_agent", "--agent-llm", "deterministic-agent",
        "--agent-llm-args", '{"temperature":0.0}',
        "--user", "user_simulator", "--user-llm", "deterministic-user",
        "--user-llm-args", '{"temperature":0.0}',
        "--num-trials", "1", "--seed", "300", "--max-concurrency", "1",
        "--max-steps", "20", "--max-errors", "10", "--save-to", save_to,
        "--a1-harness", "--a1-trace-root", trace_root, "--log-level", "WARNING",
    ]


def _run_cli(args: list[str]) -> None:
    from tau2.cli import main
    from tau2.utils.display import ConsoleDisplay

    # Rich's legacy Windows renderer can fail on a checkmark in the native
    # display layer under cp1252. Display is not execution; suppress it only
    # for this deterministic preflight so CLI/run/lifecycle behavior remains
    # the subject under test.
    with (
        patch.object(sys, "argv", args),
        patch.object(ConsoleDisplay.console, "input", return_value="y"),
        patch.object(ConsoleDisplay.console, "print"),
        patch.object(ConsoleDisplay, "display_run_config"),
        patch.object(ConsoleDisplay, "display_simulation"),
        patch.object(ConsoleDisplay, "display_agent_metrics"),
    ):
        main()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="30_a1_validation/preflight/deterministic-cli-v1",
        help="Directory relative to data/simulations; it must not already exist.",
    )
    args = parser.parse_args()
    root = DATA_DIR / "simulations" / args.output
    if root.exists():
        raise FileExistsError(f"Deterministic preflight output already exists: {root}")

    import tau2.run as run_module
    original_registry = run_module.registry
    task = original_registry.get_tasks_loader("mock")()[0]
    local_registry = LocalRegistry(task)
    run_module.registry = local_registry
    original_evaluate = run_module.evaluate_simulation
    run_module.evaluate_simulation = lambda **_kwargs: RewardInfo(reward=1.0)
    try:
        success_save = f"{args.output}/success/results"
        success_trace = f"{args.output}/success/traces"
        command = _cli_args(save_to=success_save, trace_root=success_trace, task_id=task.id)
        DeterministicToolkit.calls = 0
        _run_cli(command)
        first_environment_creations = local_registry.environment_creations
        first_tool_calls = DeterministicToolkit.calls

        trace_dir = DATA_DIR / "simulations" / success_trace
        lifecycle_path = next(trace_dir.glob("*.lifecycle.json"))
        trace_path = next(trace_dir.glob("*.jsonl"))
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        trace_lines = trace_path.read_bytes().splitlines()
        trace_valid = (
            lifecycle["trace_sha256"] == _sha256(trace_path)
            and lifecycle["trace_event_count"] == len(trace_lines)
            and lifecycle["trace_last_sequence"] == len(trace_lines) - 1
        )

        local_registry.environment_creations = 0
        DeterministicToolkit.calls = 0
        _run_cli(command)
        resume_environment_creations = local_registry.environment_creations
        resume_tool_calls = DeterministicToolkit.calls

        DeterministicAgent.fail = True
        failure_save = f"{args.output}/failure/results"
        failure_trace = f"{args.output}/failure/traces"
        try:
            _run_cli(_cli_args(save_to=failure_save, trace_root=failure_trace, task_id=task.id))
        except RuntimeError as exc:
            failure_error = str(exc)
        else:
            raise AssertionError("failure injection did not fail")
        failure_lifecycle_path = next((DATA_DIR / "simulations" / failure_trace).glob("*.lifecycle.json"))
        failure_lifecycle = json.loads(failure_lifecycle_path.read_text(encoding="utf-8"))
        failure_results = json.loads((DATA_DIR / "simulations" / f"{failure_save}.json").read_text(encoding="utf-8"))

        report = {
            "schema_version": "a1-deterministic-preflight/v1",
            "cli_command": command,
            "external_api_calls": False,
            "success": {
                "results": f"data/simulations/{success_save}.json",
                "trace": str(trace_path.relative_to(DATA_DIR.parent)).replace("\\", "/"),
                "lifecycle": str(lifecycle_path.relative_to(DATA_DIR.parent)).replace("\\", "/"),
                "run_config": str((trace_dir / "a1-run-config.json").relative_to(DATA_DIR.parent)).replace("\\", "/"),
                "environment_creations": first_environment_creations,
                "tool_calls": first_tool_calls,
                "trace_sha256": lifecycle["trace_sha256"],
                "trace_event_count": lifecycle["trace_event_count"],
                "trace_last_sequence": lifecycle["trace_last_sequence"],
                "trace_validation": trace_valid,
            },
            "resume": {
                "environment_creations": resume_environment_creations,
                "tool_calls": resume_tool_calls,
                "closed_episode_skipped": resume_environment_creations == 0 and resume_tool_calls == 0,
            },
            "failure_before_simulation_run": {
                "error": failure_error,
                "lifecycle_status": failure_lifecycle["status"],
                "serialized_simulation_count": len(failure_results["simulations"]),
            },
        }
        report_path = root / "preflight-report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(report_path)
    finally:
        DeterministicAgent.fail = False
        run_module.registry = original_registry
        run_module.evaluate_simulation = original_evaluate


if __name__ == "__main__":
    main()

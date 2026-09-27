"""Paired A2 controls for the seven previously A3-only FI pilot faults."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tau2.fault_injection.contracts import FaultKind, FaultScenario
from tau2.fault_injection.fixtures import compile_fixture, load_fixture, write_fixture
from tau2.fault_injection.runner import execute_scenario, write_scenario_plan


ROOT = Path("data/simulations/60_fault_injection/fi-v1-a3-pilot-a2-control-v1")
SOURCE = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")
AGENT = "openai/claude-sonnet-5"
USER = "openai/claude-opus-4-8"


def _first_tool_ordinal(fixture, tool_name: str) -> int:
    for index, action in enumerate(fixture.agent_actions):
        message = ((action.response or {}).get("choices") or [{}])[0].get("message") or {}
        if any(call.get("function", {}).get("name") == tool_name for call in message.get("tool_calls") or []):
            return index
    raise ValueError(f"fixture {fixture.fixture_id} lacks {tool_name}")


def prepare(root: Path) -> None:
    if root.exists():
        raise FileExistsError(f"refusing to overwrite paired A2 control root: {root}")
    root.mkdir(parents=True)
    task68 = compile_fixture(SOURCE, task_id="68", trial=0, agent_model=AGENT, user_model=USER)
    task16 = compile_fixture(SOURCE, task_id="16", trial=0, agent_model=AGENT, user_model=USER)
    write_fixture(task68, root / "fixtures" / "task-68__trial-000.json")
    write_fixture(task16, root / "fixtures" / "task-16__trial-000.json")
    lookup = _first_tool_ordinal(task68, "find_user_id_by_name_zip")
    cancel = _first_tool_ordinal(task16, "cancel_pending_order")
    definitions = (
        ("a3_fenced_json_repair", FaultKind.A3_FENCED_JSON_REPAIR, task68, lookup),
        ("a3_double_encoded_repair", FaultKind.A3_DOUBLE_ENCODED_JSON_REPAIR, task68, lookup),
        ("a3_trailing_comma_repair", FaultKind.A3_TRAILING_COMMA_REPAIR, task68, lookup),
        ("a3_truncated_json_no_repair", FaultKind.A3_TRUNCATED_JSON_NO_REPAIR, task68, lookup),
        ("a3_repaired_schema_reject", FaultKind.A3_REPAIRED_SCHEMA_REJECT, task68, lookup),
        ("a3_invalid_output_after_write", FaultKind.A3_INVALID_OUTPUT_AFTER_WRITE, task16, cancel),
        ("a3_postcondition_fail_after_write", FaultKind.A3_POSTCONDITION_FAIL_AFTER_WRITE, task16, cancel),
    )
    scenarios = [FaultScenario(
        scenario_id=name,
        fault=fault,
        fixture_id=fixture.fixture_id,
        track="a2-retry",
        target_ordinal=ordinal,
        notes="Paired A2 control; local OpenAI-compatible loopback only",
    ) for name, fault, fixture, ordinal in definitions]
    (root / "manifest.json").write_text(json.dumps({
        "experiment": "FI-v1-A3-pilot-A2-control",
        "gateway_called": False,
        "scenario_count": len(scenarios),
        "track": "a2-retry",
        "source_artifacts_unchanged": True,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "scenarios.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in scenarios], indent=2) + "\n",
        encoding="utf-8",
    )


def run(root: Path) -> None:
    scenarios = [FaultScenario.model_validate(row) for row in json.loads((root / "scenarios.json").read_text(encoding="utf-8"))]
    fixtures = {item.fixture_id: item for item in (load_fixture(path) for path in (root / "fixtures").glob("*.json"))}
    observations = []
    for scenario in scenarios:
        write_scenario_plan(root, scenario, fixtures[scenario.fixture_id])
        observations.append(execute_scenario(root, scenario, fixtures[scenario.fixture_id]).model_dump(mode="json"))
    (root / "a2-control-observations.json").write_text(
        json.dumps(observations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)(args.root)


if __name__ == "__main__":
    main()

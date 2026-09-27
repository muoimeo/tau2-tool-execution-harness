"""Run FI-v1 core profiles once through the opt-in A3 path, locally only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tau2.fault_injection.fixtures import compile_fixture, load_fixture, write_fixture
from tau2.fault_injection.plans import build_fi_v1_schedule
from tau2.fault_injection.runner import execute_scenario, write_scenario_plan


ROOT = Path("data/simulations/60_fault_injection/fi-v1-a3-core-regression-v1")
SOURCE = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")
AGENT = "openai/claude-sonnet-5"
USER = "openai/claude-opus-4-8"


def prepare(root: Path) -> None:
    if root.exists():
        raise FileExistsError(f"refusing to overwrite A3 FI-Core root: {root}")
    root.mkdir(parents=True)
    fixtures = {}
    for task_id, trial in (("68", 0), ("70", 0), ("73", 0), ("37", 1), ("79", 3)):
        fixture = compile_fixture(SOURCE, task_id=task_id, trial=trial, agent_model=AGENT, user_model=USER)
        write_fixture(fixture, root / "fixtures" / f"task-{task_id}__trial-{trial:03d}.json")
        fixtures[task_id] = fixture
    scenarios = [
        row.model_copy(update={
            "track": "a3-recovery",
            "scenario_id": row.scenario_id.replace("a2-retry", "a3-recovery"),
        })
        for row in build_fi_v1_schedule(fixtures)
        if row.track == "a2-retry"
    ]
    (root / "manifest.json").write_text(json.dumps({
        "experiment": "FI-v1-A3-core-regression",
        "gateway_called": False,
        "scenario_count": len(scenarios),
        "track": "a3-recovery",
        "source_artifacts_unchanged": True,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "scenarios.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in scenarios], indent=2) + "\n",
        encoding="utf-8",
    )


def run(root: Path) -> None:
    from tau2.fault_injection.contracts import FaultScenario
    scenarios = [FaultScenario.model_validate(item) for item in json.loads((root / "scenarios.json").read_text(encoding="utf-8"))]
    fixtures = {item.fixture_id: item for item in (load_fixture(path) for path in (root / "fixtures").glob("*.json"))}
    observations = []
    for scenario in scenarios:
        write_scenario_plan(root, scenario, fixtures[scenario.fixture_id])
        observations.append(execute_scenario(root, scenario, fixtures[scenario.fixture_id]).model_dump(mode="json"))
    (root / "core-observations.json").write_text(
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

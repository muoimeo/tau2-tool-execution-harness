"""Prepare or explicitly run the local-only FI-v1 controlled experiment.

This script never calls the corporate gateway. ``prepare`` only writes new
fixture/schedule artifacts. ``run-one`` executes a single approved scenario
against a loopback OpenAI-compatible server.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import yaml

from tau2.fault_injection.catalog import catalog_rows
from tau2.fault_injection.analysis import summarize, write_trace_derived_tool_calls
from tau2.fault_injection.contracts import FaultScenario
from tau2.fault_injection.fixtures import compile_fixture, load_fixture, write_fixture
from tau2.fault_injection.plans import build_fi_v1_schedule
from tau2.fault_injection.runner import execute_scenario, write_scenario_plan


DEFAULT_ROOT = Path("data/simulations/60_fault_injection/fi-v1-retail-local-provider-v1")
DEFAULT_A0 = Path("data/simulations/20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json")
AGENT = "openai/claude-sonnet-5"
USER = "openai/claude-opus-4-8"


def prepare(root: Path, source: Path) -> None:
    if root.exists():
        raise FileExistsError(f"FI root already exists; refusing to overwrite: {root}")
    fixtures_dir = root / "fixtures"
    root.mkdir(parents=True)
    fixtures = {}
    for task_id, trial in (("68", 0), ("70", 0), ("73", 0), ("37", 1), ("79", 3)):
        fixture = compile_fixture(source, task_id=task_id, trial=trial, agent_model=AGENT, user_model=USER)
        write_fixture(fixture, fixtures_dir / f"task-{task_id}__trial-{trial:03d}.json")
        fixtures[task_id] = fixture
    schedule = build_fi_v1_schedule(fixtures)
    (root / "fixture-index.json").write_text(json.dumps({
        task_id: {"fixture_id": item.fixture_id, "fixture_sha256": item.fixture_sha256,
                  "source_results_sha256": item.source_results_sha256}
        for task_id, item in fixtures.items()
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (root / "scenario-matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["scenario_id", "fault", "fixture_id", "track", "target_role", "target_ordinal", "notes"])
        writer.writeheader()
        writer.writerows(item.model_dump() for item in schedule)
    (root / "fault-catalog.yaml").write_text(yaml.safe_dump(catalog_rows(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / "manifest.yaml").write_text(yaml.safe_dump({
        "experiment_id": "FI-v1-retail-local-provider",
        "classification": "controlled_execution_layer_experiment",
        "evidence_only": True,
        "included_in_benchmark_metrics": False,
        "provider_transport": "local_openai_compatible_loopback",
        "gateway_called": False,
        "agent": AGENT,
        "user": USER,
        "tracks": ["a0-native", "a1-validation", "a2-retry"],
        "scenario_count": len(schedule),
        "source_results": str(source),
        "source_benchmark_remains_unchanged": True,
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Prepared {len(schedule)} FI-v1 scenarios at {root}")


def run_one(root: Path, scenario_id: str) -> None:
    rows = list(csv.DictReader((root / "scenario-matrix.csv").open(encoding="utf-8")))
    matches = [row for row in rows if row["scenario_id"] == scenario_id]
    if len(matches) != 1:
        raise ValueError(f"scenario_id must name exactly one scheduled FI scenario: {scenario_id}")
    scenario = FaultScenario.model_validate({
        **matches[0], "target_ordinal": int(matches[0]["target_ordinal"]),
    })
    fixture_paths = list((root / "fixtures").glob("*.json"))
    fixture = next((load_fixture(path) for path in fixture_paths if load_fixture(path).fixture_id == scenario.fixture_id), None)
    if fixture is None:
        raise ValueError(f"scheduled fixture missing: {scenario.fixture_id}")
    write_scenario_plan(root, scenario, fixture)
    print(execute_scenario(root, scenario, fixture).model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepared = sub.add_parser("prepare")
    prepared.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    prepared.add_argument("--a0-results", type=Path, default=DEFAULT_A0)
    one = sub.add_parser("run-one")
    one.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    one.add_argument("--scenario-id", required=True)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    analysis.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.a0_results)
    elif args.command == "run-one":
        run_one(args.root, args.scenario_id)
    else:
        output_dir = args.output_dir or args.root / "analysis"
        summary = summarize(args.root, output_dir / "scenario-summary.csv")
        calls = write_trace_derived_tool_calls(args.root, output_dir / "trace-derived-tool-calls.json")
        print(f"Wrote derived analysis: {summary}, {calls}")


if __name__ == "__main__":
    main()

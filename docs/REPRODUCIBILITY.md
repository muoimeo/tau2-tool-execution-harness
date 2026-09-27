# Reproducing the Retail execution-harness study

This document describes this fork's A0 (native), A1 (validation), A2 (provider
retry), A3 (bounded recovery), and local fault-injection tracks. Run commands
from the repository root. The commands below use Windows CMD syntax, as used
for the frozen runs. Do not reuse an existing run root: resume validates its
frozen config and keeps prior attempts.

## What a clone contains

- Source, Retail fixture DB/tasks/policy, tests, experiment scripts, and
  architecture/metric documentation are versioned.
- `data/simulations/` contains generated transcripts, traces, lifecycle
  files, and analysis. It is intentionally Git-ignored. A clone cannot
  recalculate the published natural-run numbers until those raw artifacts are
  supplied separately or the experiment is rerun. The
  [artifact index](ARTIFACT_INDEX.md) records the final result-file checksums.
- The live model pair is an organization gateway configuration. An independent
  rerun requires access to equivalent model endpoints and credentials.
  Temperature 0 does not guarantee identical transcripts across time.
- `pyproject.toml` sets minimum dependency versions; there is no committed
  environment lock. Record `python --version` and `pip freeze` with a new run
  if exact environment reconstruction is required.

The offline suite was last verified locally with Python 3.11.5, LiteLLM
1.65.4, Pydantic 2.13.4, pytest 9.1.1, and PyYAML 6.0.3. These are an
environment snapshot, not a fully locked dependency graph.

## Install and verify without a gateway

```cmd
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pytest -q -m offline_deterministic --strict-markers
```

The offline tests mock or block provider calls. They exercise A1-A3 contracts,
runtime boundaries, resume behavior, and fault-injection components. They do
not recreate the live benchmark scores.

## Frozen Retail-58 protocol

```text
Task IDs: 8 9 14 16 17 18 20 21 22 23 24 25 28 35 37 38 39 40
          44 46 48 50 52 53 55 58 60 61 62 63 64 68 70 71 73
          74 75 76 77 78 79 80 82 84 86 87 88 89 91 93 95 96
          101 103 104 107 110 113
Trials: 4                  Root seed: 300
Trial seeds: 626729, 373753, 361454, 1567
Agent: openai/claude-sonnet-5
User simulator: openai/claude-opus-4-8
Temperature: 0.0           Max concurrency: 1
Max steps: 200             Max errors: 10
Planned logical episodes: 58 × 4 = 232
```

The native benchmark provides the Retail task order. The task subset and
trial seeds must remain fixed across tracks. Evaluator and Retail task data
are not edited for the harness experiments.

## Live tracks

The existing frozen CMD wrappers contain the complete model arguments,
gateway URL, task IDs, output roots, and A1/A2/A3 flags. Inspect the wrapper
and its `_run_..._once.cmd` target before starting a new run. On a clean clone,
after gateway credentials are configured for LiteLLM:

| Track | Entrypoint | Generated result root under `data/simulations/` |
|---|---|---|
| A0 | `scripts\resume_a0_retail_stratified58.cmd` | `20_a0_native_baseline/a0-main/retail-stratified-58-v1/` |
| A1 | `scripts\resume_a1_retail_stratified58_4trials.cmd` | `30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/` |
| A2 | `scripts\resume_a2_retail_stratified58_4trials_v4.cmd` | `40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability/` |
| A3 | `scripts\resume_a3_retail_stratified58_4trials_v3.cmd` | `50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/` |

For the A0 wrapper, first create its empty root because it expects the
directory to exist:

```cmd
mkdir data\simulations\20_a0_native_baseline\a0-main\retail-stratified-58-v1
.\scripts\resume_a0_retail_stratified58.cmd
```

For A1-A3, invoke the listed wrapper directly from the repository root.
They preserve attempt artifacts and may require manual authorization for an
unsafe post-WRITE failure. Authorization is scoped to a specific
`task:trial:failed_attempt`; see each wrapper's usage comment. Never add an
authorization token without inspecting the failed lifecycle and trace.

The A3 v3 planning script can create its frozen manifest, taskset, config
preview, and scheduled ledger on a clean root before the A3 run:

```cmd
.venv\Scripts\python.exe scripts\prepare_a3_retail_stratified58_4trials_v3.py prepare
.venv\Scripts\python.exe scripts\prepare_a3_retail_stratified58_4trials_v3.py verify
```

Output files have different locations across tracks. A0/A2/A3 generally use
`results/results.json`; A1 uses `results.json` at its run root. Harnessed
tracks have episode `traces/*.jsonl`, `*.lifecycle.json`, and for A2/A3
`*.provider-retry.jsonl`; A3 adds `*.recovery.jsonl`. A0 uses post-run passive
telemetry (`scripts/a0_passive_telemetry.py`). The
[metric catalogue](../analysis/METRIC_CATALOG_A0_A3_HOW_TO_CALCULATE.md)
specifies the exact fields and denominators. Incomplete operational attempts
are not benchmark reward-zero episodes.

## Controlled fault injection without a gateway

FI uses an A0 transcript as the source for a frozen fixture, then a local
OpenAI-compatible scripted server for A0-F/A1-F/A2-F/A3-F. Thus FI requires
an A0 `results.json`, whether from a fresh A0 run or a separately supplied
artifact. Example with a new FI root:

```cmd
.venv\Scripts\python.exe scripts\fi_v1.py prepare --root data\simulations\60_fault_injection\reproduction-v1 --a0-results data\simulations\20_a0_native_baseline\a0-main\retail-stratified-58-v1\results\results.json
.venv\Scripts\python.exe scripts\fi_v1.py run-one --root data\simulations\60_fault_injection\reproduction-v1 --scenario-id p3_unknown_tool__task68__a1-validation
.venv\Scripts\python.exe scripts\fi_v1.py analyze --root data\simulations\60_fault_injection\reproduction-v1
```

The FI command targets one scheduled scenario. The full catalog and A3 pilot
are described in [FI-v1 design](architecture/fault_injection_fi_v1.md) and
`scripts/a3_fi_pilot.py`. FI results establish behavior under controlled
faults; they are not benchmark Pass¹/Pass⁴ scores.

## Limits on comparison

Natural A0-A3 runs use the same frozen task set/model pair, but the provider
and user simulator can still produce different trajectories. A2/A3 can have
operationally unresolved episodes absent from `results.json`. Report
completion separately and use only completed episodes for benchmark outcome
rates. Use FI for mechanism-level containment and recovery claims; do not
infer a causal increase in task success from cross-run Pass¹ alone.

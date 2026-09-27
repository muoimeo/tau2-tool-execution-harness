# Experiment scripts

Run these from the repository root. The scripts generate artifacts below
`data/simulations/`, which Git ignores. See
[the reproduction guide](../docs/REPRODUCIBILITY.md) before a live run.

| Purpose | Scripts to use |
|---|---|
| A0 native run and post-run analysis | `resume_a0_retail_stratified58.cmd`, `a0_passive_telemetry.py`, `build_ben03_a0_main_analysis.py` |
| A1 final run | `resume_a1_retail_stratified58_4trials.cmd`, `_run_a1_retail_stratified58_4trials_once.cmd`, `_tee_cmd_output.ps1`, `a1_remaining_episodes.py`, `build_a1_main_58x4_summary.py` |
| A2 final run | `resume_a2_retail_stratified58_4trials_v4.cmd`, `_run_a2_retail_stratified58_4trials_v4_once.cmd`, shared `resume_a2_retail_stratified58_1trial_v2.cmd`, `a2_resume_audit.ps1` |
| A3 final run | `prepare_a3_retail_stratified58_4trials_v3.py`, `resume_a3_retail_stratified58_4trials_v3.cmd`, `_run_a3_retail_stratified58_4trials_v3_once.cmd`, shared A2 resume wrapper and audit helper |
| Local controlled FI | `fi_v1.py`, `a3_fi_pilot.py`, `a3_fi_pilot_a2_control.py`, `a3_fi_core_regression.py`, `analyze_a3_fi_pilot.py`, `reconcile_fi_a0_a3_coverage.py` |
| Diagnostic and manual annotation | `analyze_provider_retry_diagnostics.py`, `run_provider_empty_diagnostic.py`, `run_live_structural_empty_probe.py`, `build_res06_a1_a3_review_queues.py`, `review_res06_a1_a3.py` |

The preflight and earlier A0 review scripts remain for their recorded stage.
Historical A2/A3 v1/v2 run wrappers were removed because their experimental
roots were interrupted or superseded. The v4 A2 and v3 A3 wrappers target the
final runs. Their hardcoded output paths must be changed to a *new* root for a
new protocol; never point them at an already completed artifact directory.

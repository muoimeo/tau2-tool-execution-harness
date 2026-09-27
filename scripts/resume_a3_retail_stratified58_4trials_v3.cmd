@echo off
setlocal EnableExtensions

REM Frozen A3 Main v3. Optional one-use manual authorization:
REM   .\scripts\resume_a3_retail_stratified58_4trials_v3.cmd TASK:TRIAL:FAILED_ATTEMPT
set "A2_ARTIFACT_DIR=data\simulations\50_a3_validation_retry_recovery\a3-main\retail-stratified-58-v1-4trials-v3-per-logical-safe-failures"
set "A2_EXPECTED_RESULTS=232"
set "A2_RUN_COMMAND=scripts\_run_a3_retail_stratified58_4trials_v3_once.cmd"
set "A2_RUN_LABEL=A3 Main 58x4 v3 (per-logical safe failures)"
set "A2_MAX_WRAPPER_INVOCATIONS=500"
call scripts\resume_a2_retail_stratified58_1trial_v2.cmd %*
exit /b %ERRORLEVEL%

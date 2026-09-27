@echo off
setlocal EnableExtensions

REM Frozen A2 Main 58 tasks x 4 trials, structural observability v4.
REM Optional one-use authorization: TASK:TRIAL:FAILED_ATTEMPT.
set "A2_ARTIFACT_DIR=data\simulations\40_a2_validation_retry\a2-main\retail-stratified-58-v1-4trials-v4-structural-observability"
set "A2_EXPECTED_RESULTS=232"
set "A2_RUN_COMMAND=scripts\_run_a2_retail_stratified58_4trials_v4_once.cmd"
set "A2_RUN_LABEL=A2 Main 58x4 v4 structural observability"
set "A2_MAX_WRAPPER_INVOCATIONS=500"
call scripts\resume_a2_retail_stratified58_1trial_v2.cmd %*
exit /b %ERRORLEVEL%

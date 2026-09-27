@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM External run-resume wrapper for the A1 58-task staging batch.
REM It does not retry a model request inside an episode and does not alter
REM tau2, task data, evaluator, reward, or raw results. It only restarts the
REM native CLI after the known provider empty-AssistantMessage process exit.

set "MAX_EMPTY_ASSISTANT_RETRIES=50"
set "WAIT_SECONDS=30"
set "ATTEMPT=0"
set "ARTIFACT_DIR=data\simulations\30_a1_validation\a1-staging\retail-stratified-58-v1-1trial"
set "RESULTS=%ARTIFACT_DIR%\results"
set "TRACE_ROOT=%ARTIFACT_DIR%\traces"
set "LOG_DIR=%ARTIFACT_DIR%\logs"
set "ATTEMPT_LOG=%ARTIFACT_DIR%\resume-attempts.log"
set "EMPTY_ASSISTANT_TEXT=AssistantMessage must have either content or tool calls"

if not exist "%ARTIFACT_DIR%" (
  echo ERROR: expected A1 artifact directory not found: %ARTIFACT_DIR%
  exit /b 2
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:resume
set /a ATTEMPT+=1
set "RUN_LOG=%LOG_DIR%\resume-attempt-!ATTEMPT!.log"
echo.
echo ================================================================
echo A1 58x1 resume attempt !ATTEMPT! of %MAX_EMPTY_ASSISTANT_RETRIES%
echo ================================================================
echo [%DATE% %TIME%] start attempt=!ATTEMPT! >> "%ATTEMPT_LOG%"

REM The y is consumed only if tau2 asks to resume its existing results.json.
REM A1 validates closed episodes and skips them; an incomplete lifecycle is
REM retired and rerun as a new episode_attempt from a fresh Environment.
REM The helper mirrors output to screen and RUN_LOG, then returns tau2's code.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\_tee_cmd_output.ps1" -CommandFile "scripts\_run_a1_retail_stratified58_1trial_once.cmd" -RunLog "!RUN_LOG!"
set "EXIT_CODE=!ERRORLEVEL!"
echo [%DATE% %TIME%] end attempt=!ATTEMPT! exit_code=!EXIT_CODE! log=!RUN_LOG! >> "%ATTEMPT_LOG%"

if "!EXIT_CODE!"=="0" goto complete

REM Tee-Object on Windows PowerShell writes UTF-16 by default; use
REM Select-String rather than findstr so known provider failures are detected
REM regardless of the attempt-log encoding.
powershell -NoProfile -Command "if (Select-String -LiteralPath '!RUN_LOG!' -SimpleMatch -Quiet -Pattern '%EMPTY_ASSISTANT_TEXT%') { exit 0 } else { exit 1 }"
if errorlevel 1 goto unexpected_failure

echo [%DATE% %TIME%] known_empty_assistant_message=true >> "%ATTEMPT_LOG%"
if !ATTEMPT! GEQ %MAX_EMPTY_ASSISTANT_RETRIES% goto stopped

echo.
echo Known empty AssistantMessage provider failure. Native checkpoints retained.
echo Waiting %WAIT_SECONDS% seconds before CLI resume...
timeout /t %WAIT_SECONDS% /nobreak >nul
goto resume

:complete
echo.
echo A1 58x1 CLI completed successfully.
echo [%DATE% %TIME%] completed after !ATTEMPT! wrapper attempt(s) >> "%ATTEMPT_LOG%"
exit /b 0

:unexpected_failure
echo.
echo STOPPED: tau2 failed with exit code !EXIT_CODE!, but the per-attempt log did not contain the known empty AssistantMessage error.
echo Inspect "%RUN_LOG%" and "%ATTEMPT_LOG%". No automatic restart was performed.
echo [%DATE% %TIME%] stopped_unexpected_exit=!EXIT_CODE! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

:stopped
echo.
echo STOPPED: reached %MAX_EMPTY_ASSISTANT_RETRIES% known empty-AssistantMessage failures.
echo Inspect "%ATTEMPT_LOG%" before continuing.
echo [%DATE% %TIME%] stopped_after_limit=!ATTEMPT! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

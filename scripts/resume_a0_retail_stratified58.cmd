@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM External process-resume wrapper for the native A0 CLI.
REM It does not alter tau2 source, tasks, evaluator, model arguments, or results.json.

set "MAX_RESUME_ATTEMPTS=50"
set "WAIT_SECONDS=30"
set "ATTEMPT=0"
set "ARTIFACT_DIR=data\simulations\20_a0_native_baseline\a0-main\retail-stratified-58-v1"
set "ATTEMPT_LOG=%ARTIFACT_DIR%\resume-attempts.log"

if not exist "%ARTIFACT_DIR%" (
  echo ERROR: expected A0 artifact directory not found: %ARTIFACT_DIR%
  exit /b 2
)

:resume
set /a ATTEMPT+=1
echo.
echo ================================================================
echo Native A0 resume attempt !ATTEMPT! of %MAX_RESUME_ATTEMPTS%
echo ================================================================
echo [%DATE% %TIME%] start attempt=!ATTEMPT! >> "%ATTEMPT_LOG%"

REM The y is consumed only by tau2's native "resume? (y/n)" prompt.
echo y| tau2 run --domain retail --task-ids 8 9 14 16 17 18 20 21 22 23 24 25 28 35 37 38 39 40 44 46 48 50 52 53 55 58 60 61 62 63 64 68 70 71 73 74 75 76 77 78 79 80 82 84 86 87 88 89 91 93 95 96 101 103 104 107 110 113 --agent-llm "openai/claude-sonnet-5" --user-llm "openai/claude-opus-4-8" --agent-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --user-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --num-trials 4 --seed 300 --max-concurrency 1 --max-steps 200 --max-errors 10 --save-to "20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results" --log-level INFO
set "EXIT_CODE=!ERRORLEVEL!"
echo [%DATE% %TIME%] end attempt=!ATTEMPT! exit_code=!EXIT_CODE! >> "%ATTEMPT_LOG%"

if "!EXIT_CODE!"=="0" goto complete

if !ATTEMPT! GEQ %MAX_RESUME_ATTEMPTS% goto stopped

echo.
echo tau2 exited with code !EXIT_CODE!. Native checkpoints are retained.
echo Waiting %WAIT_SECONDS% seconds before the next native resume attempt...
timeout /t %WAIT_SECONDS% /nobreak >nul
goto resume

:complete
echo.
echo A0 native CLI completed successfully.
echo [%DATE% %TIME%] completed after !ATTEMPT! wrapper attempt(s) >> "%ATTEMPT_LOG%"
exit /b 0

:stopped
echo.
echo STOPPED: reached %MAX_RESUME_ATTEMPTS% failed wrapper attempts.
echo Inspect %ATTEMPT_LOG% and the terminal output before continuing.
echo [%DATE% %TIME%] stopped_after_limit=!ATTEMPT! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM External run-resume wrapper for frozen A1-Main Retail 58 x 4.
REM It never retries inside an episode. It invokes the same native CLI against
REM the same output root; A1 itself skips committed closed episodes and reruns
REM incomplete attempts from a fresh Environment with a new attempt number.

set "EXPECTED_EPISODES=232"
set "MAX_EMPTY_ASSISTANT_RETRIES=200"
set "WAIT_SECONDS=30"
set "ATTEMPT=0"
set "ARTIFACT_DIR=data\simulations\30_a1_validation\a1-main\retail-stratified-58-v1-4trials-v1"
set "RESULTS_JSON=%ARTIFACT_DIR%\results.json"
set "TRACE_ROOT=%ARTIFACT_DIR%\traces"
set "LOG_DIR=%ARTIFACT_DIR%\logs"
set "ATTEMPT_LOG=%ARTIFACT_DIR%\resume-attempts.log"
set "EMPTY_ASSISTANT_TEXT=AssistantMessage must have either content or tool calls"

if not exist "%ARTIFACT_DIR%" mkdir "%ARTIFACT_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
REM Continue numbering at max(existing)+1. Counting files could overwrite a
REM gap (for example logs 1 and 3 would otherwise create log 3 again).
for /f %%A in ('powershell -NoProfile -Command "$n=Get-ChildItem -LiteralPath '%LOG_DIR%' -Filter 'resume-attempt-*.log' -ErrorAction SilentlyContinue ^| ForEach-Object { if ($_.BaseName -match '^resume-attempt-(\d+)$') { [int]$Matches[1] } }; if ($n) { ($n ^| Measure-Object -Maximum).Maximum } else { 0 }"') do set "ATTEMPT=%%A"

:resume
set /a ATTEMPT+=1
set "RUN_LOG=%LOG_DIR%\resume-attempt-!ATTEMPT!.log"
call :remaining
set "REMAINING_BEFORE=!REMAINING!"
echo.
echo ================================================================
echo A1-Main Retail 58x4 resume attempt !ATTEMPT! of %MAX_EMPTY_ASSISTANT_RETRIES%
echo Remaining unique episodes before attempt: !REMAINING_BEFORE! of %EXPECTED_EPISODES%
echo ================================================================
echo [%DATE% %TIME%] start attempt=!ATTEMPT! remaining_before=!REMAINING_BEFORE! >> "%ATTEMPT_LOG%"

REM Output remains visible and is also captured in an attempt-local log.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\_tee_cmd_output.ps1" -CommandFile "scripts\_run_a1_retail_stratified58_4trials_once.cmd" -RunLog "!RUN_LOG!"
set "EXIT_CODE=!ERRORLEVEL!"
call :remaining
set "REMAINING_AFTER=!REMAINING!"
echo [%DATE% %TIME%] end attempt=!ATTEMPT! exit_code=!EXIT_CODE! remaining_after=!REMAINING_AFTER! log=!RUN_LOG! >> "%ATTEMPT_LOG%"

if "!EXIT_CODE!"=="0" (
  if not "!REMAINING_AFTER!"=="0" goto integrity_failure
  .\.venv\Scripts\python.exe scripts\build_a1_main_58x4_summary.py --root "%ARTIFACT_DIR%"
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto integrity_failure
  goto complete
)

REM Never auto-resume an integrity/evaluator failure, even if an earlier log
REM line also mentions the known empty assistant response.
powershell -NoProfile -Command "$p='!RUN_LOG!'; $patterns=@('Tool call:', 'TraceLoggerError', 'TraceInvariantError', 'closed A1 lifecycle/trace mismatch', 'serialized A1 result has no verified closed trace/lifecycle', 'duplicate dispatch', 'duplicate trace phase', 'A1EntrypointError'); if (Select-String -LiteralPath $p -SimpleMatch -Quiet -Pattern $patterns) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto integrity_failure

powershell -NoProfile -Command "if (Select-String -LiteralPath '!RUN_LOG!' -SimpleMatch -Quiet -Pattern '%EMPTY_ASSISTANT_TEXT%') { exit 0 } else { exit 1 }"
if errorlevel 1 goto unexpected_failure

echo [%DATE% %TIME%] known_empty_assistant_message=true >> "%ATTEMPT_LOG%"
if !ATTEMPT! GEQ %MAX_EMPTY_ASSISTANT_RETRIES% goto stopped
echo.
echo Known empty AssistantMessage provider failure. Checkpoints retained; resuming in %WAIT_SECONDS% seconds.
timeout /t %WAIT_SECONDS% /nobreak >nul
goto resume

:remaining
set "REMAINING=UNKNOWN"
set "REMAINING_FILE=%LOG_DIR%\remaining-attempt-!ATTEMPT!.txt"
.\.venv\Scripts\python.exe scripts\a1_remaining_episodes.py --results "%RESULTS_JSON%" --expected %EXPECTED_EPISODES% > "%REMAINING_FILE%"
set "COUNT_EXIT=!ERRORLEVEL!"
if exist "%REMAINING_FILE%" set /p REMAINING=<"%REMAINING_FILE%"
if not "!COUNT_EXIT!"=="0" set "REMAINING=UNKNOWN"
exit /b

:complete
echo.
echo A1-Main Retail 58x4 completed: 232 unique serialized episodes.
echo [%DATE% %TIME%] completed attempts=!ATTEMPT! remaining_after=0 >> "%ATTEMPT_LOG%"
exit /b 0

:integrity_failure
if "!EXIT_CODE!"=="0" set "EXIT_CODE=3"
echo.
echo STOPPED: evaluator mismatch, trace/lifecycle integrity issue, duplicate-execution signal, or an incomplete successful exit was detected.
echo Inspect "%RUN_LOG%" and "%ATTEMPT_LOG%". No automatic restart was performed.
echo [%DATE% %TIME%] stopped_integrity_or_incomplete_exit exit_code=!EXIT_CODE! remaining_after=!REMAINING_AFTER! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

:unexpected_failure
echo.
echo STOPPED: tau2 exited with code !EXIT_CODE!, but the log did not contain the known empty AssistantMessage error.
echo Inspect "%RUN_LOG%" and "%ATTEMPT_LOG%". No automatic restart was performed.
echo [%DATE% %TIME%] stopped_unexpected_exit=!EXIT_CODE! remaining_after=!REMAINING_AFTER! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

:stopped
echo.
echo STOPPED: reached %MAX_EMPTY_ASSISTANT_RETRIES% known empty-AssistantMessage interruptions.
echo Inspect "%ATTEMPT_LOG%" before continuing.
echo [%DATE% %TIME%] stopped_after_limit=!ATTEMPT! remaining_after=!REMAINING_AFTER! >> "%ATTEMPT_LOG%"
exit /b !EXIT_CODE!

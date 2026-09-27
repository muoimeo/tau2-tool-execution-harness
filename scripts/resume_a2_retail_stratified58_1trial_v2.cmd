@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM A2 v2 staging resume wrapper.
REM Invoke directly: .\scripts\resume_a2_retail_stratified58_1trial_v2.cmd
REM An unsafe episode can only be authorized explicitly once, for example:
REM .\scripts\resume_a2_retail_stratified58_1trial_v2.cmd 107:0:1

if not defined A2_MAX_WRAPPER_INVOCATIONS set "A2_MAX_WRAPPER_INVOCATIONS=50"
if not defined A2_MAX_UNSAFE_MANUAL_RESUMES set "A2_MAX_UNSAFE_MANUAL_RESUMES=2"
set "MAX_RESUME_ATTEMPTS=%A2_MAX_WRAPPER_INVOCATIONS%"
set "WAIT_SECONDS=30"
if not defined A2_ARTIFACT_DIR set "A2_ARTIFACT_DIR=data\simulations\40_a2_validation_retry\a2-staging\retail-stratified-58-v1-1trial-v2"
set "ARTIFACT_DIR=%A2_ARTIFACT_DIR%"
set "RESULTS=%ARTIFACT_DIR%\results\results.json"
set "TRACE_ROOT=%ARTIFACT_DIR%\traces"
set "LOG_DIR=%ARTIFACT_DIR%\logs"
set "ATTEMPT_LOG=%ARTIFACT_DIR%\resume-attempts.log"
if not defined A2_EXPECTED_RESULTS set "A2_EXPECTED_RESULTS=58"
set "EXPECTED_RESULTS=%A2_EXPECTED_RESULTS%"
if not defined A2_RUN_COMMAND set "A2_RUN_COMMAND=scripts\_run_a2_retail_stratified58_1trial_once.cmd"
set "RUN_COMMAND=%A2_RUN_COMMAND%"
if not defined A2_RUN_LABEL set "A2_RUN_LABEL=A2 58x1 v2"
set "UNSAFE_AUTHORIZATION="
set "UNSAFE_AUTHORIZATIONS="
set "A2_NO_WRITE_AUTHORIZATIONS="
set "SAFE_DECLARATION="
for %%T in (%*) do (
    set "RAW_CONTROL=%%~T"
    if /i "!RAW_CONTROL:~0,5!"=="safe:" (
        set "SAFE_DECLARATION=!RAW_CONTROL:~5!"
    ) else if /i "!RAW_CONTROL:~0,8!"=="nowrite:" (
        if defined A2_NO_WRITE_AUTHORIZATIONS (set "A2_NO_WRITE_AUTHORIZATIONS=!A2_NO_WRITE_AUTHORIZATIONS!;!RAW_CONTROL:~8!") else set "A2_NO_WRITE_AUTHORIZATIONS=!RAW_CONTROL:~8!"
    ) else if not "!RAW_CONTROL!"=="" (
        if defined UNSAFE_AUTHORIZATIONS (set "UNSAFE_AUTHORIZATIONS=!UNSAFE_AUTHORIZATIONS!;!RAW_CONTROL!") else set "UNSAFE_AUTHORIZATIONS=!RAW_CONTROL!"
    )
)
set "A2_SKIP_ARGS="

if not "%SAFE_DECLARATION%"=="" (
    echo %SAFE_DECLARATION%| findstr /r "^[0-9][0-9]*:[0-9][0-9]*:[1-9][0-9]*$" >nul
    if errorlevel 1 (
        echo STOPPED: safe declaration must be safe:TASK:TRIAL:ATTEMPT.
        exit /b 2
    )
)

if not exist "%ARTIFACT_DIR%" mkdir "%ARTIFACT_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:resume
REM Reserve a unique log file before any preflight or provider call.  A crash
REM after reservation cannot cause the next invocation to reuse this sequence.
for /f "tokens=1-4 delims=|" %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode reserve-invocation -Ledger "%ATTEMPT_LOG%" -LogDirectory "%LOG_DIR%" -RunRoot "%ARTIFACT_DIR%" -CommandFile "!RUN_COMMAND!" -Authorization "!UNSAFE_AUTHORIZATIONS!"') do (
    set "INVOCATION_SEQUENCE=%%A"
    set "INVOCATION_ID=%%B"
    set "RUN_LOG=%%C"
    set "STARTED_AT=%%D"
)
if not defined INVOCATION_SEQUENCE (
    echo STOPPED: wrapper invocation reservation failed; no benchmark command was started.
    exit /b 1
)
echo.
echo ================================================================
echo %A2_RUN_LABEL% invocation !INVOCATION_ID! of %MAX_RESUME_ATTEMPTS%
echo ================================================================
set "EXIT_CODE=0"

REM A completed root is never submitted to tau2 again; record this wrapper
REM invocation as completed without creating an Environment or provider call.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode check-complete -Results "%RESULTS%" -TraceRoot "%TRACE_ROOT%" -ExpectedResultCount %EXPECTED_RESULTS% -MaxUnsafeManualResumes %A2_MAX_UNSAFE_MANUAL_RESUMES%
if not errorlevel 1 goto complete

REM Convert only budget-exhausted unsafe logical episodes into explicit
REM orchestration skips. This does not authorize or create an attempt.
set "A2_SKIP_ARGS="
for /f "delims=" %%S in ('powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode get-exhausted-unsafe -TraceRoot "%TRACE_ROOT%" -MaxUnsafeManualResumes %A2_MAX_UNSAFE_MANUAL_RESUMES%') do (
    set "A2_SKIP_ARGS=!A2_SKIP_ARGS! --a2-skip-unresolved %%S"
)

REM Do not auto-cross a prior unsafe attempt.  A prior unsafe attempt is
REM considered resolved only when its matching manual audit has a later closed
REM lifecycle.  The optional token permits exactly that identity for this run.
REM A no-WRITE token is separately prevalidated by tau2 from immutable trace
REM evidence. Let that preflight run before this wrapper's unsafe-only scan.
if defined A2_NO_WRITE_AUTHORIZATIONS goto after_initial_unsafe_check
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode check-unresolved-unsafe -TraceRoot "%TRACE_ROOT%" -Authorization "!UNSAFE_AUTHORIZATIONS!" -SafeDeclaration "!SAFE_DECLARATION!" -MaxUnsafeManualResumes %A2_MAX_UNSAFE_MANUAL_RESUMES%
if errorlevel 1 goto unsafe
:after_initial_unsafe_check

REM y is handled inside the wrapper; the caller runs this file directly.
set "A2_SAFE_UNRESOLVED_DECLARATION=!SAFE_DECLARATION!"
set "A2_UNSAFE_AUTHORIZATIONS=!UNSAFE_AUTHORIZATIONS!"
set "A2_NO_WRITE_AUTHORIZATIONS=!A2_NO_WRITE_AUTHORIZATIONS!"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\_tee_cmd_output.ps1" -CommandFile "!RUN_COMMAND!" -RunLog "!RUN_LOG!"
set "EXIT_CODE=!ERRORLEVEL!"
REM Never carry authorization into an automatic follow-up invocation.  Runtime
REM additionally records and consumes it in the immutable audit artifact.
set "UNSAFE_AUTHORIZATION="
set "UNSAFE_AUTHORIZATIONS="
set "SAFE_DECLARATION="
set "A2_SAFE_UNRESOLVED_DECLARATION="
set "A2_UNSAFE_AUTHORIZATIONS="
set "A2_NO_WRITE_AUTHORIZATIONS="

REM Configuration/integrity errors are not retryable operational failures.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode check-config -RunLog "%RUN_LOG%"
if errorlevel 1 goto unexpected

REM Stop on a newly unresolved unsafe lifecycle.  Historical unsafe attempts
REM that were manually audited and superseded by a closed attempt are allowed.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode check-unresolved-unsafe -TraceRoot "%TRACE_ROOT%" -MaxUnsafeManualResumes %A2_MAX_UNSAFE_MANUAL_RESUMES%
if errorlevel 1 goto unsafe

REM A zero exit is complete only when 58 unique result tuples exist.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode check-complete -Results "%RESULTS%" -TraceRoot "%TRACE_ROOT%" -ExpectedResultCount %EXPECTED_RESULTS% -MaxUnsafeManualResumes %A2_MAX_UNSAFE_MANUAL_RESUMES%
if not errorlevel 1 goto complete

if !INVOCATION_SEQUENCE! GEQ %MAX_RESUME_ATTEMPTS% goto stopped
call :finalize safe-failure-continued
echo A2 attempt incomplete or safely interrupted; artifacts retained.
echo Waiting %WAIT_SECONDS% seconds before the next resume...
timeout /t %WAIT_SECONDS% /nobreak >nul
goto resume

:complete
echo %A2_RUN_LABEL% completed with %EXPECTED_RESULTS% unique serialized episodes.
call :finalize completed
exit /b 0

:unsafe
echo STOPPED: unsafe side effect or invalid lifecycle detected. Manual audit required.
set "EXIT_CODE=20"
call :finalize unsafe-stopped
exit /b 20

:unexpected
echo STOPPED: non-retryable configuration or integrity error. Inspect "%RUN_LOG%".
if !EXIT_CODE! EQU 0 set "EXIT_CODE=1"
call :finalize error
exit /b !EXIT_CODE!

:stopped
echo STOPPED: reached %MAX_RESUME_ATTEMPTS% wrapper attempts.
echo Inspect "%ATTEMPT_LOG%" and per-attempt logs before continuing.
if !EXIT_CODE! EQU 0 set "EXIT_CODE=1"
call :finalize error
exit /b !EXIT_CODE!

:finalize
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\a2_resume_audit.ps1" -Mode finish-invocation -Ledger "%ATTEMPT_LOG%" -Sequence !INVOCATION_SEQUENCE! -InvocationId "!INVOCATION_ID!" -StartedAt "!STARTED_AT!" -ExitCode !EXIT_CODE! -Outcome %~1 -RunLog "!RUN_LOG!"
if errorlevel 1 echo WARNING: invocation terminal ledger record could not be written for !INVOCATION_ID!.
exit /b 0

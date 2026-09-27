@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM One frozen A3 Main v3 invocation; the resume wrapper owns restarts.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "AUTH_ARG="
if defined A2_UNSAFE_AUTHORIZATIONS set "AUTH_ARG=--a2-authorize-unsafe-resume %A2_UNSAFE_AUTHORIZATIONS%"
set "NO_WRITE_AUTH_ARG="
if defined A2_NO_WRITE_AUTHORIZATIONS set "NO_WRITE_AUTH_ARG=--a2-authorize-no-write-resume %A2_NO_WRITE_AUTHORIZATIONS%"
if not defined A2_SKIP_ARGS set "A2_SKIP_ARGS="

echo y| .\.venv\Scripts\tau2.exe run --domain retail --task-ids 8 9 14 16 17 18 20 21 22 23 24 25 28 35 37 38 39 40 44 46 48 50 52 53 55 58 60 61 62 63 64 68 70 71 73 74 75 76 77 78 79 80 82 84 86 87 88 89 91 93 95 96 101 103 104 107 110 113 --agent-llm "openai/claude-sonnet-5" --user-llm "openai/claude-opus-4-8" --agent-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --user-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --num-trials 4 --seed 300 --max-concurrency 1 --max-steps 200 --max-errors 10 --a1-harness --a2-retry --a3-recovery --a2-max-operational-failures 3 --a2-safe-operational-failure-scope per_logical_episode --a2-max-unsafe-manual-resumes 2 --a1-trace-root "50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/traces" %AUTH_ARG% %NO_WRITE_AUTH_ARG% %A2_SKIP_ARGS% --save-to "50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/results/results" --log-level INFO
exit /b %ERRORLEVEL%

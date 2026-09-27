@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM One frozen A2 Main v4 invocation. Resume behavior belongs to its wrapper.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "AUTH_ARG="
if defined A2_UNSAFE_AUTHORIZATIONS set "AUTH_ARG=--a2-authorize-unsafe-resume %A2_UNSAFE_AUTHORIZATIONS%"
set "SAFE_DECLARATION_ARG="
if defined A2_SAFE_UNRESOLVED_DECLARATION set "SAFE_DECLARATION_ARG=--a2-declare-safe-unresolved %A2_SAFE_UNRESOLVED_DECLARATION%"
if not defined A2_SKIP_ARGS set "A2_SKIP_ARGS="

echo y| .\.venv\Scripts\tau2.exe run --domain retail --task-ids 8 9 14 16 17 18 20 21 22 23 24 25 28 35 37 38 39 40 44 46 48 50 52 53 55 58 60 61 62 63 64 68 70 71 73 74 75 76 77 78 79 80 82 84 86 87 88 89 91 93 95 96 101 103 104 107 110 113 --agent-llm "openai/claude-sonnet-5" --user-llm "openai/claude-opus-4-8" --agent-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --user-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --num-trials 4 --seed 300 --max-concurrency 1 --max-steps 200 --max-errors 10 --a1-harness --a1-trace-root "40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability/traces" --a2-retry --a2-max-operational-failures 3 --a2-max-unsafe-manual-resumes 2 %AUTH_ARG% %SAFE_DECLARATION_ARG% %A2_SKIP_ARGS% --save-to "40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability/results/results" --log-level INFO
exit /b %ERRORLEVEL%

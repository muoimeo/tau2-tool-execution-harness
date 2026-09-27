@echo off
REM One native A1 CLI invocation. The parent resume wrapper owns retry policy.
REM The parent wrapper pipes output through PowerShell Tee-Object. Force UTF-8
REM in the child Python process so Rich can render its status glyphs safely.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo y| tau2 run --domain retail --task-ids 8 9 14 16 17 18 20 21 22 23 24 25 28 35 37 38 39 40 44 46 48 50 52 53 55 58 60 61 62 63 64 68 70 71 73 74 75 76 77 78 79 80 82 84 86 87 88 89 91 93 95 96 101 103 104 107 110 113 --agent-llm "openai/claude-sonnet-5" --user-llm "openai/claude-opus-4-8" --agent-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --user-llm-args "{\"temperature\":0.0,\"api_base\":\"https://stream-netmind.viettel.vn/aigw/ai/v1\"}" --num-trials 1 --seed 300 --max-concurrency 1 --max-steps 200 --max-errors 10 --save-to "30_a1_validation/a1-staging/retail-stratified-58-v1-1trial/results" --a1-harness --a1-trace-root "30_a1_validation/a1-staging/retail-stratified-58-v1-1trial/traces" --log-level INFO
exit /b %ERRORLEVEL%

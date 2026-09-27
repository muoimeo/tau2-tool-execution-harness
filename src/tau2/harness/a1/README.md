# A1 — validation and containment

Read this folder when the question is: “Agent proposed a tool call; may it run
against the native environment?”

Suggested reading order:

1. `contracts.py` — immutable request/result/event shapes.
2. `parser.py` and `scoped_parser.py` — provider tool-call normalization.
3. `registry.py` and `validation.py` — may this request run?
4. `dispatcher.py` — reject with zero dispatch, or invoke once.
5. `runtime.py` — joins those steps during an orchestrated episode.
6. `episode.py`, `entrypoint.py`, and `integration_contracts.py` — per-episode
   binding, lifecycle, resume, and native-message adaptation.
7. `trace.py` — append-only evidence of the tool-call path.

This folder does not contain provider retry, repair, rollback, or recovery.

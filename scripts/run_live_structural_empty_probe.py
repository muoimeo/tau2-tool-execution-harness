"""Bounded provider-only live probe for alternate assistant output fields.

The output is structural metadata only. No prompt, response text, tool
arguments, raw response, headers, or customer data is retained.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from time import perf_counter
from typing import Any
import warnings

from tau2.environment.tool import Tool
from tau2.harness.model_response_inspector import (
    inspect_model_response_structure,
    summarize_assistant_output_structure,
)
from tau2.utils import llm_utils


def make_tools() -> list[Tool]:
    tools: list[Tool] = []
    for index in range(15):
        def diagnostic_lookup(record_id: str, include_status: bool = True) -> str:
            """Read-only synthetic diagnostic lookup; never dispatched."""
            return record_id
        diagnostic_lookup.__name__ = f"diagnostic_read_{index:02d}"
        tools.append(Tool(diagnostic_lookup))
    return tools


def make_messages(call_index: int) -> list[dict[str, str]]:
    # 36 synthetic messages and approximately 10k wordpiece-like tokens. The
    # nonce makes independent generations distinct but is not persisted.
    messages = [{
        "role": "system",
        "content": "You are a read-only provider structure probe. Reply with a short acknowledgement and do not call a tool.",
    }]
    for index in range(1, 35):
        role = "user" if index % 2 else "assistant"
        messages.append({"role": role, "content": ("diagnostic " * 285) + f"turn {index}."})
    messages.append({
        "role": "user",
        "content": f"Independent diagnostic generation {call_index}. Return a short acknowledgement without a tool call.",
    })
    return messages


def is_empty(summary: dict[str, Any]) -> bool:
    return (
        not summary["message_present"]
        or (not summary["content_present"] and not summary["tool_calls_present"])
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def main() -> None:
    # LiteLLM's Pydantic serializer warning may include a response preview.
    # Suppress that display-only warning so the diagnostic's no-content
    # retention contract also holds for console output.
    warnings.filterwarnings(
        "ignore",
        message=r"Pydantic serializer warnings:.*",
        category=UserWarning,
        module=r"pydantic\.main",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--calls", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.calls <= 12:
        raise ValueError("--calls must be between 1 and 12")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise ValueError("output root must be new or empty")
    args.output_root.mkdir(parents=True, exist_ok=True)

    tools = make_tools()
    schemas = [tool.openai_schema for tool in tools]
    config = {
        "schema_version": "live-structural-empty-probe/v1",
        "model": args.model,
        "api_base_sha256": hashlib.sha256(args.api_base.encode("utf-8")).hexdigest(),
        "independent_generations": args.calls,
        "max_provider_attempts_per_generation": 3,
        "backoff_seconds": [1.0, 2.0],
        "message_count": 36,
        "tool_schema_count": 15,
        "retail_environment": False,
        "tool_dispatch": False,
        "raw_retention": "none",
    }
    (args.output_root / "probe-config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )

    attempt_path = args.output_root / "provider-attempts.jsonl"
    empty_path = args.output_root / "empty-structural-inspections.jsonl"
    outcomes = []
    empty_count = 0
    alternate_count = 0
    for generation in range(1, args.calls + 1):
        messages = make_messages(generation)
        generation_id = f"structural-probe-g{generation:04d}"
        final_outcome = "budget_exhausted"
        for attempt in range(1, 4):
            started_at = datetime.now(timezone.utc).isoformat()
            started = perf_counter()
            try:
                response = llm_utils.completion(
                    model=args.model,
                    messages=messages,
                    tools=schemas,
                    tool_choice="auto",
                    temperature=0.0,
                    api_base=args.api_base,
                    num_retries=0,
                )
            except Exception as exc:
                append_jsonl(attempt_path, {
                    "generation_id": generation_id,
                    "provider_attempt": attempt,
                    "timestamp_start": started_at,
                    "latency_ms": round((perf_counter() - started) * 1000, 3),
                    "outcome": "provider_exception",
                    "exception_type": type(exc).__name__,
                })
                final_outcome = "provider_exception"
                break

            summary = summarize_assistant_output_structure(response)
            summary.update({
                "requested_model": args.model,
                "generation_id": generation_id,
                "provider_attempt": attempt,
                "timestamp_start": started_at,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
            })
            empty = is_empty(summary)
            append_jsonl(attempt_path, {
                **summary,
                "outcome": "empty" if empty else "success",
            })
            if not empty:
                final_outcome = "success"
                break

            empty_count += 1
            alternate = any((
                summary["reasoning_content"]["present"],
                summary["reasoning"]["present"],
                summary["thinking"]["present"],
                summary["thinking_blocks_present"],
                summary["refusal"]["present"],
                summary["provider_specific_fields_present"],
            ))
            alternate_count += int(alternate)
            append_jsonl(empty_path, {
                **summary,
                "alternate_output_found": alternate,
                "structure": inspect_model_response_structure(response),
            })
            if attempt < 3:
                time.sleep(float(attempt))

        outcomes.append({"generation_id": generation_id, "outcome": final_outcome})

    conclusion = (
        "C_INCONCLUSIVE_NO_EMPTY_REPRODUCED" if empty_count == 0
        else "A_ALTERNATE_OUTPUT_FOUND" if alternate_count > 0
        else "B_NO_ALTERNATE_OUTPUT_FOUND"
    )
    (args.output_root / "probe-summary.json").write_text(
        json.dumps({
            "schema_version": "live-structural-empty-probe-summary/v1",
            "conclusion": conclusion,
            "empty_provider_attempts": empty_count,
            "empty_attempts_with_alternate_output": alternate_count,
            "outcomes": outcomes,
        }, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

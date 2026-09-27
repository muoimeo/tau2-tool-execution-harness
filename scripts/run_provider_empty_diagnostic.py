"""Controlled provider-only probe for empty-response investigation.

This deliberately does not create a Retail environment, run an orchestrator,
or dispatch a tool. It writes only safe provider-retry telemetry and a compact
probe summary. Run it only with explicit gateway approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from pathlib import Path

from tau2.data_model.message import SystemMessage, UserMessage
from tau2.environment.tool import Tool
from tau2.harness.retry import ProviderGenerationRetryController, ProviderRetrySidecar, RetryBudgetExhausted
from tau2.utils.llm_utils import generate, to_litellm_messages


def make_tools() -> list[Tool]:
    tools: list[Tool] = []
    for index in range(15):
        def probe_tool(value: str) -> str:
            """Return a diagnostic value; this function is never dispatched."""
            return value
        probe_tool.__name__ = f"diagnostic_tool_{index:02d}"
        tools.append(Tool(probe_tool))
    return tools


def probe_messages(context_tokens: int, *, profile: str, call_index: int) -> list:
    # Static, synthetic context only. The repeated token is not user/task data.
    padding = "diag " * context_tokens
    return [
        SystemMessage(role="system", content="You are a provider response-shape diagnostic. Reply only with OK."),
        # This benign nonce makes each provider invocation a distinct request
        # without retaining a prompt in artifacts. Its value is derivable only
        # from the profile and sidecar generation ID.
        UserMessage(role="user", content=f"{padding}Diagnostic invocation {profile}-{call_index}. Return OK."),
    ]


PROFILES = {
    "no-tools-short": {"tools": False, "context_tokens": 20},
    "tools-short": {"tools": True, "context_tokens": 20},
    "tools-medium": {"tools": True, "context_tokens": 2_000},
    "tools-10k": {"tools": True, "context_tokens": 10_000},
}


def main() -> None:
    # LiteLLM's current ModelResponse can emit a Pydantic serializer warning
    # containing a response preview. This probe has a no-raw-retention policy,
    # so suppress only that display warning in this standalone diagnostic
    # process. It does not alter tau2 run, provider requests, or artifacts.
    warnings.filterwarnings(
        "ignore",
        message=r"Pydantic serializer warnings:.*",
        category=UserWarning,
        module=r"pydantic\.main",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--calls", required=True, type=int)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    if args.calls < 1:
        raise ValueError("--calls must be at least one")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise ValueError("output root must be new or empty; diagnostic artifacts are append-only")

    profile = PROFILES[args.profile]
    args.output_root.mkdir(parents=True, exist_ok=True)
    sidecar_path = args.output_root / "provider-retry.jsonl"
    metadata = {
        "schema_version": "provider-empty-probe/v1",
        "telemetry_schema": "provider-observability/v1",
        "profile": args.profile,
        "calls": args.calls,
        "model": args.model,
        "api_base_sha256": hashlib.sha256(args.api_base.encode("utf-8")).hexdigest(),
        "tools_enabled": profile["tools"],
        "synthetic_context_token_target": profile["context_tokens"],
        "retail_environment": False,
        "tool_dispatch": False,
        "raw_retention": "none",
        "request_uniqueness": "static profile/call-index nonce; nonce not persisted separately",
    }
    (args.output_root / "probe-config.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )

    outcomes = []
    for index in range(1, args.calls + 1):
        messages = probe_messages(profile["context_tokens"], profile=args.profile, call_index=index)
        probe_tools = make_tools() if profile["tools"] else None
        fingerprint_payload = {
            "model": args.model,
            "temperature": 0.0,
            "messages": to_litellm_messages(messages),
            "tools": [tool.openai_schema for tool in probe_tools] if probe_tools else None,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        sidecar = ProviderRetrySidecar(
            sidecar_path,
            generation_id=f"probe-{args.profile}-g{index:04d}",
            turn_index=index,
            episode_attempt=1,
        )
        controller = ProviderGenerationRetryController(
            observer=sidecar.record,
            diagnostic_context={"request_fingerprint_sha256": fingerprint},
        )
        try:
            message = generate(
                args.model,
                messages,
                tools=probe_tools,
                provider_retry_controller=controller,
                api_base=args.api_base,
                temperature=0.0,
                _diagnostic_skip_cost=True,
                _diagnostic_no_raw_data=True,
                _diagnostic_skip_usage=True,
            )
            outcomes.append({"call": index, "outcome": "success", "has_content": bool(message.content), "has_tool_calls": bool(message.tool_calls)})
        except RetryBudgetExhausted as exc:
            outcomes.append({"call": index, "outcome": "budget_exhausted", "failure_code": exc.last_error.kind.value})

    (args.output_root / "probe-summary.json").write_text(
        json.dumps({"schema_version": "provider-empty-probe-summary/v1", "outcomes": outcomes}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

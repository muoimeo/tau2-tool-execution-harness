"""Compile a frozen FI fixture from one successful A0 transcript."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tau2.fault_injection.contracts import CompiledFixture, ProviderAction, sha256_json


class FixtureCompilationError(ValueError):
    pass


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_openai_message(message: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Convert a serialized tau2 participant message into an OpenAI message."""
    tool_calls = message.get("tool_calls")
    if tool_calls:
        openai_calls = []
        for call in tool_calls:
            openai_calls.append(
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], sort_keys=True),
                    },
                }
            )
        return {"role": "assistant", "content": None, "tool_calls": openai_calls}, "tool_calls"
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise FixtureCompilationError("provider message lacks text content and tool calls")
    return {"role": "assistant", "content": content}, "stop"


def compile_fixture(
    results_path: str | Path,
    *,
    task_id: str,
    trial: int,
    agent_model: str,
    user_model: str,
    fixture_id: str | None = None,
) -> CompiledFixture:
    """Compile provider queues without retaining source raw-data/reasoning fields."""
    path = Path(results_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = [
        item for item in payload.get("simulations", [])
        if str(item.get("task_id")) == str(task_id) and item.get("trial") == trial
    ]
    if len(matches) != 1:
        raise FixtureCompilationError(f"expected exactly one task/trial source, found {len(matches)}")
    simulation = matches[0]
    agent_actions: list[ProviderAction] = []
    user_actions: list[ProviderAction] = []
    for index, message in enumerate(simulation.get("messages", [])):
        role = message.get("role")
        # Tau2's greeting is static Orchestrator state, not an LLM invocation.
        if role == "assistant" and index == 0 and message.get("content") == "Hi! How can I help you today?":
            continue
        if role not in {"assistant", "user"}:
            continue
        openai_message, finish_reason = _as_openai_message(message)
        action = ProviderAction.response_action(openai_message, finish_reason=finish_reason)
        (agent_actions if role == "assistant" else user_actions).append(action)
    if not agent_actions or not user_actions:
        raise FixtureCompilationError("fixture needs at least one agent and user provider response")
    source_sha = _file_sha256(path)
    fixture_identity = fixture_id or f"retail-task-{task_id}-trial-{trial}"
    material = {
        "fixture_id": fixture_identity,
        "task_id": str(task_id),
        "trial": trial,
        "seed": simulation.get("seed"),
        "source_results_sha256": source_sha,
        "agent_model": agent_model,
        "user_model": user_model,
        "agent_actions": [item.model_dump(mode="json") for item in agent_actions],
        "user_actions": [item.model_dump(mode="json") for item in user_actions],
    }
    return CompiledFixture(
        fixture_id=fixture_identity,
        task_id=str(task_id),
        trial=trial,
        seed=int(simulation["seed"]),
        source_results_path=str(path),
        source_results_sha256=source_sha,
        agent_model=agent_model,
        user_model=user_model,
        agent_actions=agent_actions,
        user_actions=user_actions,
        fixture_sha256=sha256_json(material),
    )


def write_fixture(fixture: CompiledFixture, destination: str | Path) -> Path:
    """Write a canonical fixture. Existing files are never overwritten."""
    target = Path(destination)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite frozen FI fixture: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_fixture(path: str | Path) -> CompiledFixture:
    return CompiledFixture.model_validate_json(Path(path).read_text(encoding="utf-8"))

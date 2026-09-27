"""Read-only execution observation for FI-v1 native runs.

Harness-enabled FI tracks already have their normal tool trace.  This tiny
observer fills the equivalent evidence gap for A0-F without changing the
return value, exception handling, or tool arguments of ``Environment``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tau2.data_model.message import ToolCall, ToolMessage


@dataclass(frozen=True)
class ObservedNativeToolCall:
    logical_call_id: str
    tool_name: str
    requestor: str
    tool_type: str | None
    state_hash_before: str | None
    state_hash_after: str | None
    error: bool


@dataclass
class NativeExecutionObserver:
    """Wrap only one fresh Environment instance for a single FI episode."""

    calls: list[ObservedNativeToolCall] = field(default_factory=list)
    _bound: bool = field(default=False, init=False)

    def bind(self, environment: Any) -> None:
        if self._bound:
            raise RuntimeError("FI native execution observer cannot bind twice")
        original_get_response = environment.get_response

        def observed_get_response(message: ToolCall) -> ToolMessage:
            before = environment.get_db_hash()
            tool_type = None
            try:
                toolkit = environment.user_tools if message.requestor == "user" else environment.tools
                if toolkit is not None:
                    candidate = toolkit.tool_type(message.name)
                    tool_type = getattr(candidate, "value", str(candidate)).lower()
            except Exception:
                # Unknown-tool behaviour remains owned by native Environment.
                tool_type = None
            result = original_get_response(message)
            after = environment.get_db_hash()
            self.calls.append(ObservedNativeToolCall(
                logical_call_id=message.id,
                tool_name=message.name,
                requestor=message.requestor,
                tool_type=tool_type,
                state_hash_before=before,
                state_hash_after=after,
                error=result.error,
            ))
            return result

        environment.get_response = observed_get_response
        self._bound = True

    @property
    def dispatch_count(self) -> int:
        return len(self.calls)

    @property
    def write_dispatch_count(self) -> int:
        return sum(call.tool_type == "write" for call in self.calls)

    @property
    def mutation_count(self) -> int:
        return sum(
            call.tool_type == "write"
            and call.state_hash_before is not None
            and call.state_hash_before != call.state_hash_after
            for call in self.calls
        )


def summarize_harness_trace(trace_root: str | Path) -> dict[str, Any]:
    """Derive FI fields from existing HAR-06 events without modifying them."""
    completed: list[dict[str, Any]] = []
    rejected_ids: set[str] = set()
    violations: list[str] = []
    for path in sorted(Path(trace_root).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            call_id = event.get("logical_call_id")
            if event.get("phase") == "validated" and event.get("decision") == "reject":
                rejected_ids.add(call_id)
            if event.get("phase") in {"dispatch_completed", "quarantined"}:
                completed.append(event)
                if call_id in rejected_ids:
                    violations.append(f"reject_to_dispatch:{call_id}")
    writes = [event for event in completed if event.get("native_tool_type") == "write"]
    before = completed[0].get("state_hash_before") if completed else None
    after = completed[-1].get("state_hash_after") if completed else None
    return {
        "dispatch_count": len(completed),
        "write_dispatch_count": len(writes),
        "state_hash_before": before,
        "state_hash_after": after,
        "mutation_count": sum(
            event.get("state_hash_before") is not None
            and event.get("state_hash_before") != event.get("state_hash_after")
            for event in writes
        ),
        # The native observer wraps Environment.get_response and therefore is
        # intentionally bypassed by A1/A2's safe dispatcher.  ToolEvent is
        # the authoritative, already-redacted record for those tracks.
        "native_tool_calls": [
            {
                "logical_call_id": event.get("logical_call_id"),
                "tool_name": event.get("tool_name"),
                "tool_type": event.get("native_tool_type"),
                "requestor": event.get("requestor"),
                "error": event.get("dispatch_status") != "success",
                "state_hash_before": event.get("state_hash_before"),
                "state_hash_after": event.get("state_hash_after"),
            }
            for event in completed
        ],
        "invariant_violations": violations,
    }


def summarize_provider_recovery(trace_root: str | Path) -> str | None:
    """Return only the retry disposition inferred from A2's sidecar events."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(Path(trace_root).glob("*.provider-retry.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            grouped.setdefault(event["generation_id"], []).append(event)
    if any(
        any(item.get("failure_code") for item in values)
        and any(item.get("outcome") == "success" for item in values)
        for values in grouped.values()
    ):
        return "provider_retry_recovered"
    if any(any(item.get("outcome") == "budget_exhausted" for item in values) for values in grouped.values()):
        return "provider_retry_exhausted"
    return None

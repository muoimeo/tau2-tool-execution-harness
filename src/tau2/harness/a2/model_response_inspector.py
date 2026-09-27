"""Privacy-preserving structural inspection of provider response objects.

This module is diagnostic-only.  It records shape, type and cardinality, never
field values.  It must not be used to materialize an assistant response.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import re
from typing import Any


_SAFE_FIELD_NAMES = {
    "annotations",
    "audio",
    "choices",
    "content",
    "finish_reason",
    "function_call",
    "hidden_params",
    "message",
    "model",
    "provider_specific_fields",
    "reasoning",
    "reasoning_content",
    "refusal",
    "thinking",
    "thinking_blocks",
    "tool_calls",
    "usage",
}
_DO_NOT_DESCEND = {
    "arguments",
    "headers",
    "input",
    "messages",
    "prompt",
    "raw_request",
    "raw_response",
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _safe_segment(name: Any) -> str:
    """Keep known schema names; pseudonymize arbitrary/dynamic mapping keys."""
    text = str(name)
    lowered = text.lower()
    if lowered in _SAFE_FIELD_NAMES or (
        _IDENTIFIER.fullmatch(text) and not any(token in lowered for token in ("customer", "user", "email", "phone", "address", "name"))
    ):
        return text
    return f"<field-{sha256(text.encode('utf-8')).hexdigest()[:8]}>"


def _members(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}

    members: dict[str, Any] = {}
    model_fields = getattr(type(value), "model_fields", {})
    for name in model_fields:
        try:
            members[name] = getattr(value, name)
        except Exception:
            continue
    extras = getattr(value, "__pydantic_extra__", None)
    if isinstance(extras, Mapping):
        members.update({str(key): item for key, item in extras.items()})
    try:
        instance_fields = vars(value)
    except TypeError:
        instance_fields = {}
    for name, item in instance_fields.items():
        if not name.startswith("_"):
            members.setdefault(name, item)
    return members


def _record(path: str, value: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": path,
        "object_type": type(value).__name__,
        "present": True,
        "null": value is None,
    }
    if isinstance(value, str):
        row["string_length"] = len(value)
        row["boolean_presence"] = bool(value)
    elif isinstance(value, (list, tuple)):
        row["item_count"] = len(value)
        row["boolean_presence"] = bool(value)
    elif isinstance(value, Mapping):
        row["item_count"] = len(value)
        row["boolean_presence"] = bool(value)
    elif isinstance(value, bool):
        # This describes presence only and intentionally does not expose value.
        row["boolean_presence"] = True
    elif value is None:
        row["boolean_presence"] = False
    return row


def inspect_model_response_structure(
    response: Any, *, max_depth: int = 8, max_nodes: int = 512
) -> list[dict[str, Any]]:
    """Return a deterministic, value-free structural description.

    Sequence items are addressed by ordinal. Sensitive request/raw branches are
    represented by their container record but are never traversed.
    """
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(path: str, value: Any, depth: int) -> None:
        if len(rows) >= max_nodes:
            return
        rows.append(_record(path, value))
        if value is None or depth >= max_depth:
            return
        if isinstance(value, (str, bytes, int, float, bool)):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)

        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(f"{path}[{index}]", item, depth + 1)
            return

        for raw_name, item in sorted(_members(value).items()):
            safe_name = _safe_segment(raw_name)
            child_path = f"{path}.{safe_name}"
            rows.append(_record(child_path, item))
            if raw_name.lower() not in _DO_NOT_DESCEND:
                # The child record was already emitted; recurse into only its
                # children to avoid duplicate rows.
                if item is not None and not isinstance(item, (str, bytes, int, float, bool)):
                    if isinstance(item, (list, tuple)):
                        for index, nested in enumerate(item):
                            visit(f"{child_path}[{index}]", nested, depth + 2)
                    else:
                        for nested_name, nested in sorted(_members(item).items()):
                            nested_safe = _safe_segment(nested_name)
                            visit(f"{child_path}.{nested_safe}", nested, depth + 2)

    visit("response", response, 0)
    # Path ordering makes output stable across object __dict__ insertion order.
    unique = {row["path"]: row for row in rows}
    return [unique[path] for path in sorted(unique)]


def summarize_assistant_output_structure(response: Any) -> dict[str, Any]:
    """Return the approved compact output-shape summary for a live probe.

    Unlike :func:`inspect_model_response_structure`, this summary includes a
    small allowlist of diagnostic scalar metadata (IDs, model, finish reason,
    token count). It never includes output text or arbitrary field values.
    """

    def field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    def length(value: Any) -> int | None:
        return len(value) if isinstance(value, (str, list, tuple, Mapping)) else None

    choices = field(response, "choices", None)
    choice_count = len(choices) if isinstance(choices, (list, tuple)) else None
    choice = choices[0] if choice_count else None
    message = field(choice, "message", None)

    def presence(name: str) -> dict[str, Any]:
        value = field(message, name, None)
        return {"present": bool(value), "length_or_count": length(value)}

    provider_fields = field(message, "provider_specific_fields", None)
    message_model_fields = set(getattr(type(message), "model_fields", {})) if message is not None else set()
    extras = getattr(message, "__pydantic_extra__", None)
    if not isinstance(extras, Mapping):
        extras = {}
    # Some response implementations expose additional instance attributes
    # without Pydantic extras. Count their types without exposing field names.
    try:
        instance_fields = vars(message) if message is not None else {}
    except TypeError:
        instance_fields = {}
    extra_values = list(extras.values()) + [
        value
        for name, value in instance_fields.items()
        if not name.startswith("_") and name not in message_model_fields and name not in extras
    ]
    extra_type_counts: dict[str, int] = {}
    for value in extra_values:
        kind = type(value).__name__
        extra_type_counts[kind] = extra_type_counts.get(kind, 0) + 1

    usage = field(response, "usage", None)
    output_tokens = field(usage, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = field(usage, "output_tokens", None)

    content = field(message, "content", None)
    tool_calls = field(message, "tool_calls", None)
    annotations = field(message, "annotations", None)
    thinking_blocks = field(message, "thinking_blocks", None)
    return {
        "response_type": type(response).__name__,
        "choices_count": choice_count,
        "message_present": message is not None,
        "content_present": bool(content),
        "content_length": len(content) if isinstance(content, str) else None,
        "tool_calls_present": bool(tool_calls),
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, (list, tuple)) else 0 if tool_calls is None else None,
        "reasoning_content": presence("reasoning_content"),
        "reasoning": presence("reasoning"),
        "thinking": presence("thinking"),
        "thinking_blocks_present": bool(thinking_blocks),
        "thinking_blocks_count": len(thinking_blocks) if isinstance(thinking_blocks, (list, tuple)) else 0 if thinking_blocks is None else None,
        "refusal": presence("refusal"),
        "annotations_count": len(annotations) if isinstance(annotations, (list, tuple)) else 0 if annotations is None else None,
        "provider_specific_fields_present": bool(provider_fields),
        "provider_specific_fields_child_count": len(provider_fields) if isinstance(provider_fields, Mapping) else None,
        "extra_pydantic_fields_present": bool(extra_values),
        "extra_pydantic_field_count": len(extra_values),
        "extra_pydantic_type_counts": dict(sorted(extra_type_counts.items())),
        "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
        "finish_reason": field(choice, "finish_reason", None),
        "completion_id": field(response, "id", None),
        "resolved_model": field(response, "model", None),
    }

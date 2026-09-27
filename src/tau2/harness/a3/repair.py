"""Lossless, one-shot A3 representation repair rules."""

from __future__ import annotations

import hashlib
import json
import re

from tau2.harness.a3.contracts import RepairCandidate, RepairType
from tau2.harness.a1.parser import ParserFailure


_FENCE = re.compile(r"^\s*```(?:json)?\s*\r?\n(?P<body>[\s\S]*?)\r?\n?\s*```\s*$", re.IGNORECASE)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _remove_trailing_commas(value: str) -> str | None:
    """Remove comma tokens immediately before ] or } outside JSON strings."""
    output: list[str] = []
    changed = False
    quoted = escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            cursor = index + 1
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            if cursor < len(value) and value[cursor] in "]}":
                changed = True
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output) if changed else None


class A3Repairer:
    """Try exactly one declared repair; never invent business data."""

    def try_repair(self, failure: ParserFailure) -> RepairCandidate | None:
        original = failure.raw_call.raw_arguments_text
        if not isinstance(original, str):
            return None
        candidate: str | None = None
        repair_type: RepairType | None = None
        match = _FENCE.fullmatch(original)
        if match is not None:
            candidate, repair_type = match.group("body"), RepairType.MARKDOWN_CODE_FENCE
        else:
            try:
                decoded = json.loads(original)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, str):
                candidate, repair_type = decoded, RepairType.DOUBLE_ENCODED_JSON_OBJECT
            else:
                candidate = _remove_trailing_commas(original)
                repair_type = RepairType.TRAILING_COMMA if candidate is not None else None
        if candidate is None or repair_type is None or candidate == original:
            return None
        return RepairCandidate(
            repair_type=repair_type,
            original_payload_sha256=_sha(original),
            repaired_payload_sha256=_sha(candidate),
            repaired_payload=candidate,
        )

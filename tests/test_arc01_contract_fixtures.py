"""Validate ARC-01A machine-readable contract fixtures without HAR runtime."""

import hashlib
import json
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "arc01"


def test_parser_rejection_fixture_ids_are_deterministic():
    spec = json.loads((FIXTURES / "parser_rejection_spec.json").read_text())
    prefix = spec["synthetic_call_id"]["prefix"]
    length = spec["synthetic_call_id"]["hex_characters"]
    for case in spec["cases"]:
        data = case["input"]
        canonical = "\0".join(
            [
                "toolcall-parse-v2",
                data["run_id"],
                str(data["turn_index"]),
                str(data["malformed_call_ordinal"]),
                data["provider_call_id"],
                data["expected_requestor"],
                data["raw_tool_name"],
                data["raw_arguments_text"],
            ]
        )
        expected_id = prefix + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]
        assert case["expected"]["synthetic_call_id"] == expected_id
        assert case["expected"]["requestor"] == data["expected_requestor"]
        assert case["expected"]["dispatch_attempted"] is False
        assert case["expected"]["native_dispatch_allowed"] is False


def test_rejection_fixture_enforces_no_dispatch_and_canonical_tool_message():
    examples = json.loads((FIXTURES / "rejection_contract_examples.json").read_text())
    template = examples["canonical_tool_message_template"]
    for case in examples["cases"]:
        dispatch = case["dispatch_result"]
        result = case["validation_result"]
        message = case["tool_message"]
        assert result["decision"] == "reject"
        assert dispatch["dispatch_attempted"] is False
        assert dispatch["dispatch_count"] == 0
        assert dispatch["status"] == "not_dispatched"
        assert message["role"] == "tool"
        assert message["error"] is True
        assert message["id"] == case["request"]["logical_call_id"]
        assert message["content"] == template.replace("<CODE>", result["failure_code"])


def test_clean_allow_fixture_requires_exactly_one_dispatch_without_repair():
    examples = json.loads((FIXTURES / "clean_allow_contract_examples.json").read_text())
    for case in examples["cases"]:
        assert case["expected_validation"]["decision"] == "allow"
        assert case["expected_validation"]["arguments_unchanged"] is True
        assert case["expected_dispatch"]["dispatch_attempted"] is True
        assert case["expected_dispatch"]["dispatch_count"] == 1
        assert case["expected_dispatch"]["native_arguments_equal_request_arguments"] is True

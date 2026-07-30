"""HAR-03 parser/normalizer contracts without registry, schema, or dispatch."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tau2.harness.parser as parser_module
from tau2.harness.contracts import HarnessErrorCode, Requestor, TrustedRouteContext
from tau2.harness.parser import (
    ParserNormalizer,
    RawToolCall,
    SyntheticIdCollisionError,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "har03" / "parser_cases.json"
ROUTE = TrustedRouteContext(route_id="agent-model-response", requestor=Requestor.ASSISTANT)


def parse(raw_calls, *, route=ROUTE):
    return ParserNormalizer().parse(
        tuple(RawToolCall(**raw) for raw in raw_calls),
        run_id="har03-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=4, trusted_route=route, parsed_at=NOW,
    )


def test_parser_fixtures_preserve_raw_arguments_and_normalize_only_json_objects(monkeypatch):
    fixtures = json.loads(FIXTURES.read_text())
    original_loads = parser_module.json.loads
    calls = []

    def counting_loads(value, *args, **kwargs):
        calls.append(value)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(parser_module.json, "loads", counting_loads)
    for case in fixtures["cases"]:
        result = parse(case["raw_calls"])
        expected = case["expected"]
        assert len(result.requests) == expected["requests"]
        assert len(result.failures) == expected["failures"]
        if result.failures:
            assert [f.malformed_call_ordinal for f in result.failures] == expected["failure_ordinals"]
            assert len({f.synthetic_call_id for f in result.failures}) == len(result.failures)
            assert all(f.request_sha256 for f in result.failures)
    raw_valid = fixtures["cases"][0]["raw_calls"][0]["raw_arguments_text"]
    assert calls.count(raw_valid) == 1
    assert all(request.raw_arguments_text is not None for request in parse(fixtures["cases"][0]["raw_calls"]).requests)


def test_invalid_requestor_metadata_is_batch_error_without_tool_call_request():
    result = parse([
        {"provider_call_id": "call-1", "raw_tool_name": "get_order_details", "raw_arguments_text": "{}"}
    ], route=None)
    assert result.boundary_error is not None
    assert result.boundary_error.error_code == HarnessErrorCode.PARSE_REQUESTOR_METADATA_INVALID
    assert result.requestor is None
    assert result.requests == ()
    assert result.failures == ()


def test_malformed_call_creates_failure_not_tool_call_request():
    result = parse([
        {"provider_call_id": "bad-1", "raw_tool_name": "get_order_details", "raw_arguments_text": "{"}
    ])
    assert result.requests == ()
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.error_code == HarnessErrorCode.PARSE_MALFORMED_JSON
    assert failure.raw_call.raw_arguments_text == "{"
    assert failure.synthetic_call_id.startswith("hparse_")


def test_truncated_synthetic_id_collision_is_detected_fail_closed(monkeypatch):
    monkeypatch.setattr(parser_module, "synthetic_parser_rejection_id", lambda **_: "hparse_collision")
    with pytest.raises(SyntheticIdCollisionError, match="PARSE_SYNTHETIC_ID_COLLISION"):
        parse([
            {"provider_call_id": "bad-1", "raw_tool_name": "get_order_details", "raw_arguments_text": "{"},
            {"provider_call_id": "bad-2", "raw_tool_name": "get_order_details", "raw_arguments_text": "["},
        ])

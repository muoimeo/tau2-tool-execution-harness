"""HAR-04 strict schema and one approved Retail constraint, without dispatch."""

import inspect
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from tau2.domains.retail.environment import get_environment
from tau2.harness.contracts import HarnessErrorCode, HarnessStage, Requestor, ToolCallRequest, ValidationDecision
from tau2.harness.registry import build_registry_snapshot
from tau2.harness.validation import StrictSchemaDomainValidator, _schema_code


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "har04" / "schema_domain_cases.json"


def retail_target(tool_name: str):
    env = get_environment()
    native_tool = next(tool for tool in env.get_tools() if tool.name == tool_name)
    snapshot = build_registry_snapshot([(Requestor.ASSISTANT, env.tools)])
    resolved = snapshot.find(Requestor.ASSISTANT, tool_name)
    assert resolved is not None
    return native_tool, resolved


def request(tool_name: str, arguments: dict) -> ToolCallRequest:
    return ToolCallRequest.parsed(
        run_id="har04-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=6, logical_call_id=f"provider-{tool_name}", requestor=Requestor.ASSISTANT,
        tool_name=tool_name, arguments=arguments, provider_call_id=None,
        raw_arguments_text=json.dumps(arguments), created_at=NOW,
    )


def validate(tool_name: str, arguments: dict):
    native_tool, resolved = retail_target(tool_name)
    call = request(tool_name, arguments)
    before = call.arguments_json
    result = StrictSchemaDomainValidator.validate(call, resolved, params_model=native_tool.params, validated_at=NOW)
    assert call.arguments_json == before
    assert result.arguments_unchanged is True
    assert result.dispatch_attempted is False
    assert result.dispatch_count == 0
    return result


def test_current_pydantic_supports_direct_strict_and_extra_keywords_without_adapter():
    signature = inspect.signature(BaseModel.model_validate)
    assert "strict" in signature.parameters and "extra" in signature.parameters

    class IntegerParams(BaseModel):
        quantity: int

    assert IntegerParams.model_validate({"quantity": 2}, strict=True, extra="forbid").quantity == 2
    with pytest.raises(ValidationError):
        IntegerParams.model_validate({"quantity": "2"}, strict=True, extra="forbid")
    with pytest.raises(ValidationError):
        IntegerParams.model_validate({"quantity": 2, "extra": 1}, strict=True, extra="forbid")


def test_clean_fixture_cases_pass_schema_then_domain_deterministically():
    fixture = json.loads(FIXTURES.read_text())
    for case in fixture["clean"]:
        result = validate(case["tool_name"], deepcopy(case["arguments"]))
        assert result.decision == ValidationDecision.ALLOW, case["case_id"]
        assert result.passed_stages == (HarnessStage.SCHEMA, HarnessStage.DOMAIN_CONSTRAINT)
        repeat = validate(case["tool_name"], deepcopy(case["arguments"]))
        assert result.model_dump_json() == repeat.model_dump_json()


def test_invalid_fixture_cases_reject_with_expected_code_and_never_dispatch():
    fixture = json.loads(FIXTURES.read_text())
    for case in fixture["invalid"]:
        result = validate(case["tool_name"], deepcopy(case["arguments"]))
        assert result.decision == ValidationDecision.REJECT, case["case_id"]
        assert result.failure_code.value == case["failure_code"]
        assert result.violations
        assert all("input" not in violation.metadata_json for violation in result.violations)


def test_schema_failure_does_not_run_domain_rule(monkeypatch):
    native_tool, resolved = retail_target("modify_pending_order_items")
    called = False

    def forbidden_domain_rule(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("domain rule must not run after schema failure")

    monkeypatch.setattr(StrictSchemaDomainValidator, "_approved_domain_violation", forbidden_domain_rule)
    result = StrictSchemaDomainValidator.validate(
        request("modify_pending_order_items", {"order_id": "#W0000000"}),
        resolved, params_model=native_tool.params, validated_at=NOW,
    )
    assert result.failed_stage == HarnessStage.SCHEMA
    assert called is False


def test_native_model_schema_and_openai_schema_are_not_mutated():
    native_tool, resolved = retail_target("get_order_details")
    before_config = deepcopy(dict(native_tool.params.model_config))
    before_schema = deepcopy(native_tool.params.model_json_schema())
    before_openai_schema = deepcopy(native_tool.openai_schema)
    source_arguments = {"order_id": "#W0000000"}
    source_before = deepcopy(source_arguments)
    result = StrictSchemaDomainValidator.validate(request("get_order_details", source_arguments), resolved, params_model=native_tool.params, validated_at=NOW)
    assert result.decision == ValidationDecision.ALLOW
    assert source_arguments == source_before
    assert dict(native_tool.params.model_config) == before_config
    assert native_tool.params.model_json_schema() == before_schema
    assert native_tool.openai_schema == before_openai_schema


def test_only_native_retail_domain_rule_is_same_item_pair_check():
    result = validate("modify_pending_order_items", {
        "order_id": "#W0000000", "item_ids": ["old-1", "same", "old-3"],
        "new_item_ids": ["new-1", "same", "new-3"],
        "payment_method_id": "credit_card_0000000",
    })
    assert result.failed_stage == HarnessStage.DOMAIN_CONSTRAINT
    assert result.failure_code == HarnessErrorCode.DOMAIN_CONSTRAINT_VIOLATION
    assert result.violations[0].path == "item_ids.1"
    assert result.violations[0].constraint_id == "retail.modify_pending_order_items.item_ids_ne_new_item_ids"


def test_error_mapping_fallback_and_no_native_nested_fixture_claim():
    assert _schema_code("unexpected_error_type") == HarnessErrorCode.SCHEMA_VALIDATION_ERROR
    assert json.loads(FIXTURES.read_text())["native_nested_case_available"] is False

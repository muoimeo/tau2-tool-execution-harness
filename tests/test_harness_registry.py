"""HAR-02 deterministic registry/requestor validation with no dispatch."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tau2.domains.retail.environment import get_environment
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool
from tau2.harness.contracts import Requestor, ToolCallRequest
from tau2.harness.registry import (
    RegistryPreflightError,
    RegistryValidator,
    TrustedRouteContext,
    build_registry_snapshot,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "har02" / "registry_validation_cases.json"


class AgentToolkit(ToolKitBase):
    @is_tool(ToolType.READ)
    def agent_lookup(self, value: str) -> str:
        """Read an agent record."""
        return value


class UserToolkit(ToolKitBase):
    @is_tool(ToolType.WRITE)
    def user_lookup(self, value: str) -> str:
        """Write a user record."""
        return value


class DuplicateAgentToolkit(ToolKitBase):
    @is_tool(ToolType.READ)
    def agent_lookup(self, value: str) -> str:
        """Duplicate name used solely by the preflight test."""
        return value


def request(requestor: Requestor, tool_name: str) -> ToolCallRequest:
    return ToolCallRequest.parsed(
        run_id="har02-r0", task_id="71", trial_index=0, trial_seed=626729,
        turn_index=1, logical_call_id=f"provider-{requestor.value}-{tool_name}",
        requestor=requestor, tool_name=tool_name, arguments={"value": "x"},
        provider_call_id=None, raw_arguments_text='{"value":"x"}', created_at=NOW,
    )


def route(requestor: Requestor) -> TrustedRouteContext:
    return TrustedRouteContext(route_id=f"{requestor.value}-model-response", requestor=requestor)


def test_registry_fixtures_cover_agent_user_unknown_and_requestor_cases():
    snapshot = build_registry_snapshot([
        (Requestor.ASSISTANT, AgentToolkit()),
        (Requestor.USER, UserToolkit()),
    ])
    validator = RegistryValidator(snapshot)
    fixture = json.loads(FIXTURES.read_text())
    for case in fixture["fixtures"]:
        requestor = Requestor(case["requestor"])
        trusted = Requestor(case.get("trusted_requestor", case["requestor"]))
        result = validator.validate(
            request(requestor, case["tool_name"]), trusted_route=route(trusted), validated_at=NOW
        )
        expected = case["expected"]
        assert result.allowed is expected["allowed"], case["case_id"]
        if result.allowed:
            assert [stage.value for stage in result.passed_stages] == expected["passed_stages"]
            assert result.resolved_tool is not None
        else:
            assert result.failed_stage.value == expected["failed_stage"]
            assert result.failure_code.value == expected["failure_code"]


def test_registry_hash_is_stable_when_namespace_input_order_changes():
    agent, user = AgentToolkit(), UserToolkit()
    forward = build_registry_snapshot([(Requestor.ASSISTANT, agent), (Requestor.USER, user)])
    reverse = build_registry_snapshot([(Requestor.USER, user), (Requestor.ASSISTANT, agent)])
    assert forward.entries == reverse.entries
    assert forward.registry_sha256 == reverse.registry_sha256
    assert "_func" not in forward.model_dump_json()


def test_duplicate_name_in_one_namespace_fails_closed():
    with pytest.raises(RegistryPreflightError, match="PREFLIGHT_DUPLICATE_TOOL_NAME"):
        build_registry_snapshot([
            (Requestor.ASSISTANT, AgentToolkit()),
            (Requestor.ASSISTANT, DuplicateAgentToolkit()),
        ])


def test_native_retail_snapshot_records_native_read_write_metadata_without_dispatch():
    snapshot = build_registry_snapshot([(Requestor.ASSISTANT, get_environment().tools)])
    types = {entry.native_tool_type for entry in snapshot.entries}
    assert "read" in types
    assert "write" in types
    assert all(entry.params_schema_sha256 for entry in snapshot.entries)
    assert all("function" not in entry.params_schema_json for entry in snapshot.entries)

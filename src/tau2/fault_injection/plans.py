"""Deterministically build FI-v1 schedules from frozen compiled fixtures."""

from __future__ import annotations

from tau2.fault_injection.catalog import FI_V1_CATALOG
from tau2.fault_injection.contracts import CompiledFixture, FaultKind, FaultScenario


def _tool_names(fixture: CompiledFixture, index: int) -> set[str]:
    response = fixture.agent_actions[index].response or {}
    message = ((response.get("choices") or [{}])[0]).get("message") or {}
    return {call.get("function", {}).get("name", "") for call in message.get("tool_calls") or []}


def _first_agent_tool(fixture: CompiledFixture, name: str | None = None) -> int:
    for index in range(len(fixture.agent_actions)):
        names = _tool_names(fixture, index)
        if names and (name is None or name in names):
            return index
    raise ValueError(f"fixture {fixture.fixture_id} has no required agent tool {name!r}")


def _after_first_write(fixture: CompiledFixture) -> int:
    # FI uses task 70 where exchange_delivered_order_items is the frozen WRITE
    # in the compiled transcript. The next agent provider action is the exact
    # post-WRITE generation boundary for S1.
    write_index = _first_agent_tool(fixture, "exchange_delivered_order_items")
    if write_index + 1 >= len(fixture.agent_actions):
        raise ValueError("S1 fixture has no generation after its frozen WRITE")
    return write_index + 1


def build_fi_v1_schedule(fixtures: dict[str, CompiledFixture]) -> list[FaultScenario]:
    """Build 42 scenarios (14 profiles × A0-F/A1-F/A2-F)."""
    by_fault_fixture = {
        FaultKind.WRONG_STRICT_TYPE: fixtures["79"],
        FaultKind.DOMAIN_CONSTRAINT: fixtures["79"],
        FaultKind.PROVIDER_EXHAUST_POST_WRITE: fixtures["70"],
    }
    rows: list[FaultScenario] = []
    for definition in FI_V1_CATALOG:
        fixture = by_fault_fixture.get(definition.fault, fixtures["68"])
        if definition.fault in {
            FaultKind.MALFORMED_JSON, FaultKind.JSON_NON_OBJECT,
            FaultKind.UNKNOWN_TOOL, FaultKind.MISSING_REQUIRED_ARGUMENT,
            FaultKind.EXTRA_ARGUMENT, FaultKind.NATIVE_PRECONDITION,
        }:
            ordinal = _first_agent_tool(fixture, "find_user_id_by_name_zip")
        elif definition.fault in {FaultKind.WRONG_STRICT_TYPE, FaultKind.DOMAIN_CONSTRAINT}:
            ordinal = _first_agent_tool(fixture, "modify_pending_order_items")
        elif definition.fault == FaultKind.PROVIDER_EXHAUST_POST_WRITE:
            ordinal = _after_first_write(fixture)
        else:
            ordinal = 0
        for track in ("a0-native", "a1-validation", "a2-retry"):
            rows.append(FaultScenario(
                scenario_id=f"{definition.fault.value.lower()}__task{fixture.task_id}__{track}",
                fault=definition.fault,
                fixture_id=fixture.fixture_id,
                track=track,
                target_ordinal=ordinal,
                notes=definition.target_description,
            ))
    return rows

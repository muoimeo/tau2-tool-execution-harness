"""Frozen FI-v1 catalog and the cross-track scenario matrix."""

from __future__ import annotations

from dataclasses import dataclass

from tau2.fault_injection.contracts import FaultKind


@dataclass(frozen=True)
class FaultDefinition:
    fault: FaultKind
    boundary: str
    fixture_task_id: str
    target_description: str
    expected_a0: str
    expected_a1: str
    expected_a2: str


# D1 and D2 are intentionally anchored to existing Retail code—not invented
# examples. See RetailTools.modify_pending_order_items and
# RetailTools.find_user_id_by_name_zip.
FI_V1_CATALOG: tuple[FaultDefinition, ...] = (
    FaultDefinition(FaultKind.CLEAN_REPLAY, "provider response", "68", "unchanged compiled action", "native replay", "clean parity", "clean parity"),
    FaultDefinition(FaultKind.MALFORMED_JSON, "provider tool-call arguments", "68", "truncated JSON object", "native parser error", "reject / dispatch=0", "reject / dispatch=0"),
    FaultDefinition(FaultKind.JSON_NON_OBJECT, "provider tool-call arguments", "68", "JSON array rather than object", "native parser/model error", "reject / dispatch=0", "reject / dispatch=0"),
    FaultDefinition(FaultKind.UNKNOWN_TOOL, "provider tool-call name", "68", "fi_unknown_tool", "native environment error", "REGISTRY_UNKNOWN_TOOL / dispatch=0", "REGISTRY_UNKNOWN_TOOL / dispatch=0"),
    FaultDefinition(FaultKind.MISSING_REQUIRED_ARGUMENT, "provider tool-call arguments", "68", "find_user_id_by_name_zip without zip", "native callable error", "schema reject / dispatch=0", "schema reject / dispatch=0"),
    FaultDefinition(FaultKind.EXTRA_ARGUMENT, "provider tool-call arguments", "68", "find_user_id_by_name_zip with __fi_extra__", "native callable error", "schema reject / dispatch=0", "schema reject / dispatch=0"),
    FaultDefinition(FaultKind.WRONG_STRICT_TYPE, "provider tool-call arguments", "79", "modify_pending_order_items.item_ids is str", "native callable/type error", "schema reject / dispatch=0", "schema reject / dispatch=0"),
    FaultDefinition(FaultKind.DOMAIN_CONSTRAINT, "provider tool-call arguments", "79", "modify_pending_order_items: item_ids[i] == new_item_ids[i]", "native ValueError if dispatched", "native-equivalent domain reject / dispatch=0", "native-equivalent domain reject / dispatch=0"),
    FaultDefinition(FaultKind.NATIVE_PRECONDITION, "provider tool-call arguments", "68", "find_user_id_by_name_zip with no matching name + zip", "native 'User not found' / dispatch=1", "allow then same native error / dispatch=1", "allow then same native error / dispatch=1"),
    FaultDefinition(FaultKind.TIMEOUT_ONCE, "local HTTP transport", "68", "timeout then original action", "observe native retry", "observe native retry", "retry then recovery"),
    FaultDefinition(FaultKind.HTTP_503_ONCE, "local HTTP transport", "68", "503 then original action", "observe native retry", "observe native retry", "retry then recovery"),
    FaultDefinition(FaultKind.REASONING_ONLY_ONCE, "provider response shape", "68", "content/tool_calls absent; reasoning_content present", "empty AssistantMessage failure", "empty AssistantMessage failure", "retry then recovery"),
    FaultDefinition(FaultKind.PROVIDER_EXHAUST_PRE_WRITE, "local HTTP transport", "68", "three 503 responses before a WRITE", "observe native outcome", "observe native outcome", "failed_operational; dispatch=0"),
    FaultDefinition(FaultKind.PROVIDER_EXHAUST_POST_WRITE, "local HTTP transport", "70", "three 503 responses after an accepted WRITE", "observe native outcome", "observe native outcome", "failed_operational; no WRITE replay"),
)


def catalog_rows() -> list[dict[str, str]]:
    return [
        {
            "fault": item.fault.value,
            "boundary": item.boundary,
            "fixture_task_id": item.fixture_task_id,
            "target": item.target_description,
            "a0_expected": item.expected_a0,
            "a1_expected": item.expected_a1,
            "a2_expected": item.expected_a2,
        }
        for item in FI_V1_CATALOG
    ]

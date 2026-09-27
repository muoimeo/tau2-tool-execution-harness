"""Deterministic Retail native-error compatibility tests; no gateway."""

from pathlib import Path

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.retail.environment import get_environment
from tau2.harness.contracts import Requestor
from tau2.harness.episode import EpisodeHarnessFactory
from tau2.harness.integration_contracts import EpisodeIdentity
from tau2.harness.runtime import HarnessRuntimeBridge


@pytest.mark.parametrize(
    ("call", "expected_content"),
    [
        (
            ToolCall(
                id="missing-user", name="find_user_id_by_name_zip",
                arguments={"first_name": "Noah", "last_name": "Ito", "zip": "00000"},
            ),
            "Error: User not found",
        ),
        (
            ToolCall(id="missing-order", name="get_order_details", arguments={"order_id": "#W0000000"}),
            "Error: Order not found",
        ),
        (
            ToolCall(id="missing-product", name="get_product_details", arguments={"product_id": "0000000000"}),
            "Error: Product not found",
        ),
        (
            ToolCall(id="invalid-expression", name="calculate", arguments={"expression": "1;2"}),
            "Error: Invalid characters in expression",
        ),
    ],
)
def test_known_safe_retail_native_errors_match_native_environment_replay(
    tmp_path: Path, call: ToolCall, expected_content: str
):
    native_environment = get_environment()
    native = native_environment.get_response(call)

    identity = EpisodeIdentity(
        run_id="retail-native-error-matrix",
        task_id="matrix",
        trial_index=0,
        trial_seed=300,
        episode_attempt=1,
    )
    harness = EpisodeHarnessFactory(
        trace_root=tmp_path / call.id,
        environment_factory=get_environment,
    ).create(identity)
    context = harness.context_for_turn(turn_index=0, requestor=Requestor.ASSISTANT)
    enabled = HarnessRuntimeBridge(harness).execute_assistant_call(call, context=context)

    assert native.id == enabled.id == call.id
    assert native.role == enabled.role == "tool"
    assert native.requestor == enabled.requestor == "assistant"
    assert native.error is enabled.error is True
    assert native.content == enabled.content == expected_content

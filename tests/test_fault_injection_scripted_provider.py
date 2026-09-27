import litellm
import pytest

from tau2.data_model.message import UserMessage
from tau2.fault_injection.contracts import CompiledFixture, ProviderAction
from tau2.fault_injection.scripted_provider import LocalScriptedProvider
from tau2.utils import llm_utils


def _fixture(action: ProviderAction) -> CompiledFixture:
    return CompiledFixture(
        fixture_id="server-fixture",
        task_id="68",
        trial=0,
        seed=1,
        source_results_path="source.json",
        source_results_sha256="a" * 64,
        agent_model="openai/fi-agent",
        user_model="openai/fi-user",
        agent_actions=[action],
        user_actions=[ProviderAction.response_action({"role": "assistant", "content": "user"})],
        fixture_sha256="b" * 64,
    )


@pytest.mark.offline_deterministic
def test_generate_uses_litellm_then_local_openai_compatible_server(monkeypatch):
    fixture = _fixture(ProviderAction.response_action({"role": "assistant", "content": "local response"}))
    # The offline-suite guard blocks the default boundary. Restore LiteLLM's
    # real client deliberately; api_base points only at loopback below.
    monkeypatch.setattr(llm_utils, "completion", litellm.completion)
    with LocalScriptedProvider(fixture) as server:
        message = llm_utils.generate(
            model=fixture.agent_model,
            messages=[UserMessage(role="user", content="fixture input")],
            api_base=server.api_base,
            api_key="fi-local-key",
            timeout=3,
            num_retries=0,
        )
    assert message.content == "local response"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.model == "fi-agent"  # LiteLLM's OpenAI transport alias
    assert request.path.endswith("/chat/completions")
    assert request.message_count == 1
    assert request.tool_schema_count == 0


@pytest.mark.offline_deterministic
def test_server_returns_openai_compatible_tool_call_without_retaining_request(monkeypatch):
    action = ProviderAction.response_action(
        {"role": "assistant", "content": None, "tool_calls": [{"id": "fi-call", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]},
        finish_reason="tool_calls",
    )
    fixture = _fixture(action)
    monkeypatch.setattr(llm_utils, "completion", litellm.completion)
    with LocalScriptedProvider(fixture) as server:
        message = llm_utils.generate(
            model=fixture.agent_model,
            messages=[UserMessage(role="user", content="fixture input")],
            api_base=server.api_base,
            api_key="fi-local-key",
            timeout=3,
            num_retries=0,
        )
    assert message.tool_calls is not None
    assert message.tool_calls[0].name == "lookup"
    assert not hasattr(server.requests[0], "payload")

import json
import re
from time import perf_counter
from datetime import datetime, timezone
from typing import Any, Optional

import litellm
from litellm import completion, completion_cost
from litellm.caching.caching import Cache
from litellm.main import ModelResponse, Usage
from loguru import logger

from tau2.config import (
    DEFAULT_LLM_CACHE_TYPE,
    DEFAULT_MAX_RETRIES,
    LLM_CACHE_ENABLED,
    REDIS_CACHE_TTL,
    REDIS_CACHE_VERSION,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    REDIS_PREFIX,
    USE_LANGFUSE,
)
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool
from tau2.harness.integration_contracts import (
    OrchestratedHarnessContext,
    ParserFailureSidecar,
)
from tau2.harness.scoped_parser import parse_orchestrated_provider_tool_calls
from tau2.harness.retry import (
    ProviderFailureKind,
    ProviderGenerationError,
    ProviderGenerationRetryController,
    ProviderInvocationResult,
    canonical_provider_request_fingerprint,
    classify_provider_exception,
)
from tau2.harness.model_response_inspector import summarize_assistant_output_structure

# litellm._turn_on_debug()

if USE_LANGFUSE:
    # set callbacks
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]

litellm.drop_params = True

if LLM_CACHE_ENABLED:
    if DEFAULT_LLM_CACHE_TYPE == "redis":
        logger.info(f"LiteLLM: Using Redis cache at {REDIS_HOST}:{REDIS_PORT}")
        litellm.cache = Cache(
            type=DEFAULT_LLM_CACHE_TYPE,
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            namespace=f"{REDIS_PREFIX}:{REDIS_CACHE_VERSION}:litellm",
            ttl=REDIS_CACHE_TTL,
        )
    elif DEFAULT_LLM_CACHE_TYPE == "local":
        logger.info("LiteLLM: Using local cache")
        litellm.cache = Cache(
            type="local",
            ttl=REDIS_CACHE_TTL,
        )
    else:
        raise ValueError(
            f"Invalid cache type: {DEFAULT_LLM_CACHE_TYPE}. Should be 'redis' or 'local'"
        )
    litellm.enable_cache()
else:
    logger.info("LiteLLM: Cache is disabled")
    litellm.disable_cache()


ALLOW_SONNET_THINKING = False

if not ALLOW_SONNET_THINKING:
    logger.warning("Sonnet thinking is disabled")


def _parse_ft_model_name(model: str) -> str:
    """
    Parse the ft model name from the litellm model name.
    e.g: "ft:gpt-4.1-mini-2025-04-14:sierra::BSQA2TFg" -> "gpt-4.1-mini-2025-04-14"
    """
    pattern = r"ft:(?P<model>[^:]+):(?P<provider>\w+)::(?P<id>\w+)"
    match = re.match(pattern, model)
    if match:
        return match.group("model")
    else:
        return model


def get_response_cost(response: ModelResponse) -> float:
    """
    Get the cost of the response from the litellm completion.
    """
    response.model = _parse_ft_model_name(
        response.model
    )  # FIXME: Check Litellm, passing the model to completion_cost doesn't work.
    try:
        cost = completion_cost(completion_response=response)
    except Exception as e:
        logger.error(e)
        return 0.0
    return cost


def get_response_usage(response: ModelResponse) -> Optional[dict]:
    usage: Optional[Usage] = response.get("usage")
    if usage is None:
        return None
    return {
        "completion_tokens": usage.completion_tokens,
        "prompt_tokens": usage.prompt_tokens,
    }


def to_tau2_messages(
    messages: list[dict], ignore_roles: set[str] = set()
) -> list[Message]:
    """
    Convert a list of messages from a dictionary to a list of Tau2 messages.
    """
    tau2_messages = []
    for message in messages:
        role = message["role"]
        if role in ignore_roles:
            continue
        if role == "user":
            tau2_messages.append(UserMessage(**message))
        elif role == "assistant":
            tau2_messages.append(AssistantMessage(**message))
        elif role == "tool":
            tau2_messages.append(ToolMessage(**message))
        elif role == "system":
            tau2_messages.append(SystemMessage(**message))
        else:
            raise ValueError(f"Unknown message type: {role}")
    return tau2_messages


def to_litellm_messages(messages: list[Message]) -> list[dict]:
    """
    Convert a list of Tau2 messages to a list of litellm messages.
    """
    litellm_messages = []
    for message in messages:
        if isinstance(message, UserMessage):
            litellm_messages.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage):
            tool_calls = None
            if message.is_tool_call():
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                        "type": "function",
                    }
                    for tc in message.tool_calls
                ]
            litellm_messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": tool_calls,
                }
            )
        elif isinstance(message, ToolMessage):
            litellm_messages.append(
                {
                    "role": "tool",
                    "content": message.content,
                    "tool_call_id": message.id,
                }
            )
        elif isinstance(message, SystemMessage):
            litellm_messages.append({"role": "system", "content": message.content})
    return litellm_messages


def generate(
    model: str,
    messages: list[Message],
    tools: Optional[list[Tool]] = None,
    tool_choice: Optional[str] = None,
    harness_context: OrchestratedHarnessContext | None = None,
    parser_failure_sidecar: ParserFailureSidecar | None = None,
    provider_retry_controller: ProviderGenerationRetryController | None = None,
    a3_repairer=None,
    a3_recovery_audit=None,
    a3_repair_lineage=None,
    **kwargs: Any,
) -> UserMessage | AssistantMessage:
    """
    Generate a response from the model.

    Args:
        model: The model to use.
        messages: The messages to send to the model.
        tools: The tools to use.
        tool_choice: The tool choice to use.
        **kwargs: Additional arguments to pass to the model.

    Returns: A tuple containing the message and the cost.
    """
    # Provider-only diagnostics deliberately avoid cost calculation and raw
    # response retention. These flags are consumed here and never reach the
    # gateway; normal A0/A1/A2 execution leaves both false.
    diagnostic_skip_cost = bool(kwargs.pop("_diagnostic_skip_cost", False))
    diagnostic_no_raw_data = bool(kwargs.pop("_diagnostic_no_raw_data", False))
    diagnostic_skip_usage = bool(kwargs.pop("_diagnostic_skip_usage", False))
    if provider_retry_controller is None and kwargs.get("num_retries") is None:
        kwargs["num_retries"] = DEFAULT_MAX_RETRIES
    if provider_retry_controller is not None:
        # A2 owns every retry decision.  This makes one controller attempt
        # exactly one LiteLLM/OpenAI-compatible provider invocation.
        kwargs["num_retries"] = 0

    if model.startswith("claude") and not ALLOW_SONNET_THINKING:
        kwargs["thinking"] = {"type": "disabled"}
    litellm_messages = to_litellm_messages(messages)
    tools = [tool.openai_schema for tool in tools] if tools else None
    if tools and tool_choice is None:
        tool_choice = "auto"

    def _field(value: Any, name: str, default: Any = None) -> Any:
        """Read LiteLLM object-style and mapping-style response fields.

        This helper is observational: it does not mutate a provider object or
        normalize content/tool-call values. It exists so safe telemetry can
        distinguish an absent message from an empty one.
        """
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def _response_shape(value: Any) -> dict[str, Any]:
        choices = _field(value, "choices", None)
        choices_count = len(choices) if isinstance(choices, (list, tuple)) else None
        candidate = choices[0] if choices_count else None
        message = _field(candidate, "message", None)
        raw_tool_calls = _field(message, "tool_calls", None)
        tool_call_count = (
            None if message is None
            else len(raw_tool_calls) if isinstance(raw_tool_calls, (list, tuple))
            else 0 if raw_tool_calls is None
            else None
        )
        transport_request_id = _field(value, "_request_id", None)
        completion_id = _field(value, "id", None)
        id_source = (
            "transport_request_id" if transport_request_id is not None
            else "completion_id" if completion_id is not None
            else None
        )
        return {
            "response_type": type(value).__name__,
            "choices_count": choices_count,
            "message_present": message is not None,
            "content": _field(message, "content", None),
            "raw_tool_calls": raw_tool_calls,
            "tool_call_count": tool_call_count,
            "finish_reason": _field(candidate, "finish_reason", None),
            "completion_id": completion_id,
            "transport_request_id": transport_request_id,
            "id_source": id_source,
            # Backward-compatible alias. New analysis must use the explicit
            # provenance fields above instead of assuming this is transport ID.
            "request_id": transport_request_id or completion_id,
            "candidate": candidate,
            "message": message,
        }
    def _bucket(value: int | None) -> str | None:
        if value is None:
            return None
        for limit, label in ((4096, "0-4k"), (16384, "4k-16k"), (65536, "16k-64k")):
            if value < limit:
                return label
        return "64k+"

    def _usage_metadata(value: Any) -> dict[str, int | None]:
        usage = getattr(value, "usage", None)
        if usage is None:
            return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        def read(*names):
            for name in names:
                item = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
                if isinstance(item, int):
                    return item
            return None
        return {
            "input_tokens": read("prompt_tokens", "input_tokens"),
            "output_tokens": read("completion_tokens", "output_tokens"),
            "total_tokens": read("total_tokens"),
        }

    request_size = len(json.dumps({"messages": litellm_messages, "tools": tools}, default=str).encode("utf-8"))
    base_telemetry = {
        "requested_model": model,
        "message_count": len(litellm_messages),
        "tool_schema_count": len(tools or []),
        "request_size_bucket": _bucket(request_size),
        "request_fingerprint_sha256": canonical_provider_request_fingerprint({
            "model": model,
            "messages": litellm_messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "generation_args": {
                key: value for key, value in kwargs.items()
                if key not in {"api_key", "api_base", "base_url", "headers", "extra_headers"}
            },
        }),
    }

    def one_provider_invocation():
        timestamp_start = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        try:
            value = completion(model=model, messages=litellm_messages, tools=tools, tool_choice=tool_choice, **kwargs)
        except Exception as exc:
            telemetry = {
                **base_telemetry,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "timestamp_start": timestamp_start,
                "timestamp_end": datetime.now(timezone.utc).isoformat(),
                "http_status": getattr(exc, "status_code", None),
            }
            classified = classify_provider_exception(exc, telemetry=telemetry)
            if classified is not None:
                raise classified from exc
            raise
        shape = _response_shape(value)
        usage = _usage_metadata(value)
        telemetry = {
            **base_telemetry,
            **usage,
            "input_token_bucket": _bucket(usage["input_tokens"]),
            "resolved_model": _field(value, "model", None),
            "http_status": _field(value, "status_code", None),
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "timestamp_start": timestamp_start,
            "timestamp_end": datetime.now(timezone.utc).isoformat(),
            **{key: shape[key] for key in (
                "response_type", "choices_count", "message_present", "tool_call_count",
                "completion_id", "transport_request_id", "id_source", "request_id",
                "finish_reason",
            )},
            "content_present": bool(shape["content"]),
            "tool_calls_present": bool(shape["raw_tool_calls"] or []),
        }
        # This is deliberately before AssistantMessage construction/validation.
        if (
            shape["candidate"] is None
            or shape["message"] is None
            or (shape["content"] in (None, "") and not (shape["raw_tool_calls"] or []))
        ):
            # A2-only, read-only observability.  The response remains the
            # source of truth for the existing empty-response decision; an
            # inspector problem must never change classification or retry.
            if provider_retry_controller is not None:
                try:
                    telemetry["empty_response_structure"] = (
                        summarize_assistant_output_structure(value)
                    )
                except Exception as inspection_error:
                    logger.warning(
                        "A2 empty-response structural inspection failed: {}",
                        type(inspection_error).__name__,
                    )
            # Diagnostic-only metadata for intermittent gateway/provider empty
            # completions.  Do not retain prompt, content, tool arguments, or
            # the raw provider response.
            safe = {
                **telemetry,
            }
            if provider_retry_controller is not None:
                safe.update(provider_retry_controller.diagnostic_context)
            logger.warning("A2 empty assistant response metadata: {}", safe)
            raise ProviderGenerationError(
                ProviderFailureKind.EMPTY_ASSISTANT_MESSAGE, telemetry=telemetry
            )
        if provider_retry_controller is not None:
            return ProviderInvocationResult(value=value, telemetry=telemetry)
        return value
    try:
        response = (provider_retry_controller.execute(one_provider_invocation).value
                    if provider_retry_controller is not None else one_provider_invocation())
    except Exception as e:
        logger.error(e)
        raise
    cost = 0.0 if diagnostic_skip_cost else get_response_cost(response)
    usage = None if diagnostic_skip_usage else get_response_usage(response)
    response_shape = _response_shape(response)
    response = response_shape["candidate"]
    if response is None or response_shape["message"] is None:
        # The provider invocation has already been classified before this
        # point in normal operation. Keep a fail-closed guard for an exotic
        # object that changes shape between inspection and materialization.
        raise ValueError("provider response has no assistant choice/message")
    try:
        finish_reason = response_shape["finish_reason"]
        if finish_reason == "length":
            logger.warning("Output might be incomplete due to token limit!")
    except Exception as e:
        logger.error(e)
        raise e
    assert _field(response_shape["message"], "role", None) == "assistant", (
        "The response should be an assistant message"
    )
    content = response_shape["content"]
    raw_tool_calls = response_shape["raw_tool_calls"] or []
    if harness_context is None:
        if parser_failure_sidecar is not None:
            raise ValueError("parser_failure_sidecar requires harness_context")
        # Native path deliberately remains byte-for-byte equivalent in behavior.
        tool_calls = [
            ToolCall(
                id=_field(tool_call, "id", ""),
                name=_field(_field(tool_call, "function", {}), "name", ""),
                arguments=json.loads(_field(_field(tool_call, "function", {}), "arguments", "{}")),
            )
            for tool_call in raw_tool_calls
        ]
    else:
        if parser_failure_sidecar is None:
            raise ValueError("harness_context requires parser_failure_sidecar")
        tool_calls, _ = parse_orchestrated_provider_tool_calls(
            raw_tool_calls,
            context=harness_context,
            sidecar=parser_failure_sidecar,
            parsed_at=datetime.now(timezone.utc),
            repairer=a3_repairer,
            recovery_audit=a3_recovery_audit,
            repair_lineage=a3_repair_lineage,
        )
    tool_calls = tool_calls or None

    message = AssistantMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        cost=cost,
        usage=usage,
        raw_data=(
            None if diagnostic_no_raw_data
            else (response if isinstance(response, dict) else response.to_dict())
        ),
    )
    return message


def get_cost(messages: list[Message]) -> tuple[float, float] | None:
    """
    Get the cost of the interaction between the agent and the user.
    Returns None if any message has no cost.
    """
    agent_cost = 0
    user_cost = 0
    for message in messages:
        if isinstance(message, ToolMessage):
            continue
        if message.cost is not None:
            if isinstance(message, AssistantMessage):
                agent_cost += message.cost
            elif isinstance(message, UserMessage):
                user_cost += message.cost
        else:
            logger.warning(f"Message {message.role}: {message.content} has no cost")
            return None
    return agent_cost, user_cost


def get_token_usage(messages: list[Message]) -> dict:
    """
    Get the token usage of the interaction between the agent and the user.
    """
    usage = {"completion_tokens": 0, "prompt_tokens": 0}
    for message in messages:
        if isinstance(message, ToolMessage):
            continue
        if message.usage is None:
            logger.warning(f"Message {message.role}: {message.content} has no usage")
            continue
        usage["completion_tokens"] += message.usage["completion_tokens"]
        usage["prompt_tokens"] += message.usage["prompt_tokens"]
    return usage

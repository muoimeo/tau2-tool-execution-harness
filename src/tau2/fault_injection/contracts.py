"""Small, serializable contracts for the FI-v1 controlled experiment.

The contracts intentionally describe *provider responses*, not prompts.  This
keeps FI-v1 focused on the execution path and lets the same compiled fixture
drive A0-F, A1-F and A2-F.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON representation suitable for hashes."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class FaultKind(str, Enum):
    CLEAN_REPLAY = "C0_CLEAN_REPLAY"
    MALFORMED_JSON = "P1_MALFORMED_JSON"
    JSON_NON_OBJECT = "P2_JSON_NON_OBJECT"
    UNKNOWN_TOOL = "P3_UNKNOWN_TOOL"
    MISSING_REQUIRED_ARGUMENT = "P4_MISSING_REQUIRED_ARGUMENT"
    EXTRA_ARGUMENT = "P5_EXTRA_ARGUMENT"
    WRONG_STRICT_TYPE = "P6_WRONG_STRICT_TYPE"
    DOMAIN_CONSTRAINT = "D1_DOMAIN_CONSTRAINT"
    NATIVE_PRECONDITION = "D2_NATIVE_PRECONDITION"
    TIMEOUT_ONCE = "R1_TIMEOUT_ONCE"
    HTTP_503_ONCE = "R2_HTTP_503_ONCE"
    REASONING_ONLY_ONCE = "R3_REASONING_ONLY_ONCE"
    PROVIDER_EXHAUST_PRE_WRITE = "R4_PROVIDER_EXHAUST_PRE_WRITE"
    PROVIDER_EXHAUST_POST_WRITE = "S1_PROVIDER_EXHAUST_POST_WRITE"
    A3_FENCED_JSON_REPAIR = "A3_FENCED_JSON_REPAIR"
    A3_DOUBLE_ENCODED_JSON_REPAIR = "A3_DOUBLE_ENCODED_JSON_REPAIR"
    A3_TRAILING_COMMA_REPAIR = "A3_TRAILING_COMMA_REPAIR"
    A3_TRUNCATED_JSON_NO_REPAIR = "A3_TRUNCATED_JSON_NO_REPAIR"
    A3_REPAIRED_SCHEMA_REJECT = "A3_REPAIRED_SCHEMA_REJECT"
    A3_INVALID_OUTPUT_AFTER_WRITE = "A3_INVALID_OUTPUT_AFTER_WRITE"
    A3_POSTCONDITION_FAIL_AFTER_WRITE = "A3_POSTCONDITION_FAIL_AFTER_WRITE"


class ProviderActionKind(str, Enum):
    RESPONSE = "response"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"


class ProviderAction(BaseModel):
    """One local-server outcome for one LiteLLM provider invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProviderActionKind = ProviderActionKind.RESPONSE
    response: dict[str, Any] | None = None
    http_status: int | None = None
    error_type: str | None = None
    sleep_seconds: float | None = None

    @classmethod
    def response_action(cls, message: dict[str, Any], *, finish_reason: str = "stop") -> "ProviderAction":
        return cls(
            response={
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


class CompiledFixture(BaseModel):
    """Frozen transcript-derived provider queues, independent of A0 raw data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["fi-v1-fixture-1"] = "fi-v1-fixture-1"
    fixture_id: str
    domain: str = "retail"
    task_id: str
    trial: int
    seed: int
    source_results_path: str
    source_results_sha256: str
    agent_model: str
    user_model: str
    agent_actions: list[ProviderAction]
    user_actions: list[ProviderAction]
    fixture_sha256: str


class FaultScenario(BaseModel):
    """A frozen FI scenario and the provider-boundary fault to apply."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    fault: FaultKind
    fixture_id: str
    track: Literal["a0-native", "a1-validation", "a2-retry", "a3-recovery"]
    target_role: Literal["agent", "user"] = "agent"
    # Zero-based ordinal in the corresponding compiled provider queue.
    target_ordinal: int = 0
    notes: str = ""


class ScenarioObservation(BaseModel):
    """The compact FI-v1 result, deliberately separate from benchmark results."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    disposition: str
    provider_attempt_count: int = 0
    dispatch_count: int = 0
    write_dispatch_count: int = 0
    state_hash_before: str | None = None
    state_hash_after: str | None = None
    recovery_outcome: str | None = None
    invariant_violations: list[str] = Field(default_factory=list)

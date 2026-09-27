"""REC-02 bounded provider-generation retry policy.

The controller is intentionally outside the dispatcher.  It can retry only a
failure proven to occur before an assistant response/tool dispatch exists.  It
never retries a native tool, a harness rejection, or a domain response.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

# LiteLLM's timeout class is not a subclass of Python's built-in
# ``TimeoutError`` in the dependency version used by this repository.  Import
# it defensively so an older/newer LiteLLM without this symbol does not make
# the harness import fail.
try:  # pragma: no cover - the fallback is covered by import compatibility.
    from litellm.exceptions import Timeout as LiteLLMTimeout
except (ImportError, AttributeError):  # pragma: no cover
    LiteLLMTimeout = None

# The gateway surfaces quota exhaustion as LiteLLM's typed RateLimitError.
# Keep this import optional so a LiteLLM version change cannot disable A2.
try:  # pragma: no cover - the fallback is covered by import compatibility.
    from litellm.exceptions import RateLimitError as LiteLLMRateLimitError
except (ImportError, AttributeError):  # pragma: no cover
    LiteLLMRateLimitError = None


T = TypeVar("T")


def canonical_provider_request_fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 of a canonical request projection; raw payload is never retained."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda value: {"__type__": type(value).__name__},
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProviderFailureKind(str, Enum):
    EMPTY_ASSISTANT_MESSAGE = "PROVIDER_EMPTY_ASSISTANT_MESSAGE"
    TIMEOUT = "PROVIDER_TIMEOUT"
    RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    HTTP_502 = "PROVIDER_HTTP_502"
    HTTP_503 = "PROVIDER_HTTP_503"
    INTERRUPTION = "PROVIDER_INTERRUPTION"


class ProviderGenerationError(RuntimeError):
    """Typed, pre-dispatch provider failure eligible for policy evaluation."""

    def __init__(
        self,
        kind: ProviderFailureKind,
        message: str = "provider generation failed",
        *,
        telemetry: dict[str, Any] | None = None,
    ):
        self.kind = kind
        self.telemetry = dict(telemetry or {})
        super().__init__(message)


def classify_provider_exception(
    error: BaseException, *, telemetry: dict[str, Any] | None = None
) -> ProviderGenerationError | None:
    """Classify only structured/type-based pre-dispatch transport failures."""
    timeout_types = (TimeoutError,) if LiteLLMTimeout is None else (TimeoutError, LiteLLMTimeout)
    if isinstance(error, timeout_types):
        return ProviderGenerationError(
            ProviderFailureKind.TIMEOUT, type(error).__name__, telemetry=telemetry
        )
    status = getattr(error, "status_code", None)
    rate_limit_types = () if LiteLLMRateLimitError is None else (LiteLLMRateLimitError,)
    if isinstance(error, rate_limit_types) or status == 429:
        return ProviderGenerationError(
            ProviderFailureKind.RATE_LIMIT, type(error).__name__, telemetry=telemetry
        )
    # These are transport failures only when the client exposes a structured
    # HTTP status.  Do not inspect the exception message: gateways often put
    # HTML or provider text there, which is neither stable nor safe evidence.
    if status == 502:
        return ProviderGenerationError(
            ProviderFailureKind.HTTP_502, type(error).__name__, telemetry=telemetry
        )
    if status == 503:
        return ProviderGenerationError(
            ProviderFailureKind.HTTP_503, type(error).__name__, telemetry=telemetry
        )
    if isinstance(error, ConnectionError):
        return ProviderGenerationError(
            ProviderFailureKind.INTERRUPTION, type(error).__name__, telemetry=telemetry
        )
    return None


class RetryBudgetExhausted(RuntimeError):
    """The last eligible provider generation failure exhausted the budget."""

    def __init__(self, last_error: ProviderGenerationError, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"retry budget exhausted after {attempts} provider attempts: {last_error.kind.value}")


@dataclass(frozen=True)
class RetryPolicy:
    """Frozen A2-v1 policy: initial call plus at most two retries.

    Backoff is deterministic (1s, then 2s) rather than jittered so fault tests
    and trace interpretation remain reproducible.  The runtime adapter owns
    sleeping; tests pass a no-op sleeper.
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 2.0
    retryable: frozenset[ProviderFailureKind] = frozenset(ProviderFailureKind)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff must be non-negative")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= base_backoff_seconds")

    def delay_before_retry(self, failed_attempt: int) -> float:
        if failed_attempt < 1:
            raise ValueError("failed_attempt is one-based")
        return min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** (failed_attempt - 1)))


@dataclass(frozen=True)
class RetryAttempt:
    attempt_index: int
    failure_kind: ProviderFailureKind | None
    retry_scheduled: bool
    backoff_seconds: float
    telemetry: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderInvocationResult(Generic[T]):
    """One successful provider value plus safe diagnostic metadata only."""

    value: T
    telemetry: dict[str, Any]


@dataclass(frozen=True)
class RetryOutcome(Generic[T]):
    value: T
    attempts: tuple[RetryAttempt, ...]


class ProviderGenerationRetryController:
    """Execute one provider-generation operation with bounded typed retries."""

    def __init__(self, policy: RetryPolicy = RetryPolicy(), sleeper: Callable[[float], None] | None = None, observer=None, diagnostic_context: dict | None = None):
        self._policy = policy
        # Production A2 must honor its frozen 1s/2s backoff.  Deterministic
        # tests inject a no-op/fake sleeper explicitly.
        self._sleeper = sleeper or time.sleep
        self._observer = observer or (lambda _attempt: None)
        self._diagnostic_context = dict(diagnostic_context or {})
        self._active_attempt: int | None = None

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    @property
    def diagnostic_context(self) -> dict:
        """Safe lineage metadata only; never prompts, arguments, or response text."""
        value = dict(self._diagnostic_context)
        value["provider_attempt"] = self._active_attempt
        return value

    def execute(self, operation: Callable[[], T]) -> RetryOutcome[T]:
        attempts: list[RetryAttempt] = []
        for attempt_index in range(1, self._policy.max_attempts + 1):
            self._active_attempt = attempt_index
            try:
                value = operation()
            except ProviderGenerationError as exc:
                retry = exc.kind in self._policy.retryable and attempt_index < self._policy.max_attempts
                delay = self._policy.delay_before_retry(attempt_index) if retry else 0.0
                attempts.append(
                    RetryAttempt(
                        attempt_index, exc.kind, retry, delay,
                        telemetry={**self._diagnostic_context, **exc.telemetry},
                    )
                )
                self._observer(attempts[-1])
                if not retry:
                    if exc.kind in self._policy.retryable:
                        raise RetryBudgetExhausted(exc, attempt_index) from exc
                    raise
                self._sleeper(delay)
                continue
            if isinstance(value, ProviderInvocationResult):
                attempts.append(
                    RetryAttempt(
                        attempt_index, None, False, 0.0,
                        telemetry={**self._diagnostic_context, **value.telemetry},
                    )
                )
                value = value.value
            else:
                attempts.append(
                    RetryAttempt(
                        attempt_index, None, False, 0.0,
                        telemetry=dict(self._diagnostic_context),
                    )
                )
            self._observer(attempts[-1])
            return RetryOutcome(value=value, attempts=tuple(attempts))
        raise AssertionError("retry loop must return or raise")


class ProviderRetrySidecar:
    """Append-only, per-episode provider retry lineage; not a ToolEvent."""
    def __init__(self, path: Path, *, generation_id: str, turn_index: int, episode_attempt: int):
        self.path, self.generation_id, self.turn_index, self.episode_attempt = Path(path), generation_id, turn_index, episode_attempt
        self._sequence = (len(self.path.read_bytes().splitlines()) + 1) if self.path.exists() else 1
    def record(self, attempt: RetryAttempt) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema_version": "provider-observability/v1",
            "sequence": self._sequence,
            "generation_id": self.generation_id,
            "turn_index": self.turn_index,
            "episode_attempt": self.episode_attempt,
            "provider_attempt": attempt.attempt_index,
            "failure_code": attempt.failure_kind.value if attempt.failure_kind else None,
            "backoff_seconds": attempt.backoff_seconds,
            "outcome": "retry_scheduled" if attempt.retry_scheduled else ("success" if attempt.failure_kind is None else "budget_exhausted"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        row.update(_safe_attempt_telemetry(attempt.telemetry))
        with self.path.open("a", encoding="utf-8", newline="\n") as f: f.write(json.dumps(row, sort_keys=True, separators=(",",":"))+"\n"); f.flush()
        self._sequence += 1


_TELEMETRY_KEYS = {
    "requested_model", "resolved_model", "request_id", "http_status",
    "finish_reason", "latency_ms", "input_tokens", "output_tokens",
    "total_tokens", "message_count", "tool_schema_count",
    "request_size_bucket", "input_token_bucket", "content_present",
    "tool_calls_present",
    # v3 adds only scalar response-shape/provenance diagnostics.  It never
    # serializes prompt text, assistant text, tool arguments, raw JSON, or raw
    # HTTP headers.
    "response_type", "choices_count", "message_present", "tool_call_count",
    "completion_id", "transport_request_id", "id_source", "request_fingerprint_sha256",
    "task_id", "trial", "timestamp_start", "timestamp_end",
}

_EMPTY_STRUCTURE_KEYS = {
    "response_type", "choices_count", "message_present",
    "content_present", "content_length", "tool_calls_present",
    "tool_call_count", "thinking_blocks_present", "thinking_blocks_count",
    "annotations_count", "provider_specific_fields_present",
    "provider_specific_fields_child_count", "extra_pydantic_fields_present",
    "extra_pydantic_field_count", "output_tokens", "finish_reason",
    "completion_id", "resolved_model",
}
_EMPTY_STRUCTURE_NESTED_KEYS = {
    "reasoning_content", "reasoning", "thinking", "refusal",
}


def _safe_empty_response_structure(value: Any) -> dict[str, Any] | None:
    """Whitelist the read-only structural inspector's value-free summary."""
    if not isinstance(value, dict):
        return None
    result = {
        key: item
        for key, item in value.items()
        if key in _EMPTY_STRUCTURE_KEYS and (item is None or isinstance(item, (str, int, float, bool)))
    }
    for key in _EMPTY_STRUCTURE_NESTED_KEYS:
        item = value.get(key)
        if isinstance(item, dict):
            result[key] = {
                nested_key: nested_value
                for nested_key, nested_value in item.items()
                if nested_key in {"present", "length_or_count"}
                and (nested_value is None or isinstance(nested_value, (bool, int)))
            }
    type_counts = value.get("extra_pydantic_type_counts")
    if isinstance(type_counts, dict) and all(
        isinstance(key, str) and isinstance(count, int)
        for key, count in type_counts.items()
    ):
        result["extra_pydantic_type_counts"] = dict(sorted(type_counts.items()))
    return result


def _safe_attempt_telemetry(value: dict[str, Any] | None) -> dict[str, Any]:
    """Whitelist scalar/bucket diagnostics; never serialize raw provider data."""
    if not value:
        return {}
    result: dict[str, Any] = {}
    for key in _TELEMETRY_KEYS:
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            result[key] = item
    structure = _safe_empty_response_structure(value.get("empty_response_structure"))
    if structure is not None:
        result["empty_response_structure"] = structure
    return result

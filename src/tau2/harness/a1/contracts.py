"""Frozen HAR-01 contracts for A1 validation/containment.

These models and helpers are pure contracts. They deliberately do not import
or call the tau2 LLM parser, orchestrator, environment dispatcher, Retail tools
or gateway. Runtime integration is deferred beyond HAR-01.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONTRACT_TIMEZONE_ERROR = "datetime must include an explicit timezone"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ContractModel(BaseModel):
    """Strict, immutable envelope used by every HAR-01 contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Requestor(str, Enum):
    ASSISTANT = "assistant"
    USER = "user"


class TrustedRouteContext(ContractModel):
    """Caller-owned route context, established before provider parsing."""

    route_id: str = Field(min_length=1)
    requestor: Requestor

    @field_validator("route_id")
    @classmethod
    def nonempty_route_id(cls, value: str):
        return _nonempty(value, "route_id")


class RequestSourceKind(str, Enum):
    PARSED_PROVIDER_CALL = "parsed_provider_call"
    SYNTHETIC_PARSER_REJECTION = "synthetic_parser_rejection"


class ParseStatus(str, Enum):
    PARSED = "parsed"
    REJECTED = "rejected"


class HarnessStage(str, Enum):
    PARSE = "parse"
    REGISTRY = "registry"
    REQUESTOR = "requestor"
    SCHEMA = "schema"
    DOMAIN_CONSTRAINT = "domain_constraint"
    DECISION = "decision"
    DISPATCH = "dispatch"
    NATIVE_TOOL = "native_tool"
    RESULT = "result"
    TELEMETRY = "telemetry"


class HarnessErrorCode(str, Enum):
    PARSE_MALFORMED_JSON = "PARSE_MALFORMED_JSON"
    PARSE_ARGUMENTS_NOT_OBJECT = "PARSE_ARGUMENTS_NOT_OBJECT"
    PARSE_RESPONSE_STRUCTURE_INVALID = "PARSE_RESPONSE_STRUCTURE_INVALID"
    PARSE_REQUESTOR_METADATA_INVALID = "PARSE_REQUESTOR_METADATA_INVALID"
    PARSE_SYNTHETIC_ID_COLLISION = "PARSE_SYNTHETIC_ID_COLLISION"
    REGISTRY_UNKNOWN_TOOL = "REGISTRY_UNKNOWN_TOOL"
    REGISTRY_WRONG_REQUESTOR = "REGISTRY_WRONG_REQUESTOR"
    PREFLIGHT_DUPLICATE_TOOL_NAME = "PREFLIGHT_DUPLICATE_TOOL_NAME"
    SCHEMA_MISSING_REQUIRED_ARG = "SCHEMA_MISSING_REQUIRED_ARG"
    SCHEMA_EXTRA_ARG = "SCHEMA_EXTRA_ARG"
    SCHEMA_EXTRA_ARGUMENT = "SCHEMA_EXTRA_ARGUMENT"
    SCHEMA_WRONG_ARG_TYPE = "SCHEMA_WRONG_ARG_TYPE"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    SCHEMA_CONSTRAINT_VIOLATION = "SCHEMA_CONSTRAINT_VIOLATION"
    DOMAIN_CONSTRAINT_VIOLATION = "DOMAIN_CONSTRAINT_VIOLATION"
    EXECUTION_DUPLICATE_DISPATCH = "EXECUTION_DUPLICATE_DISPATCH"
    UNKNOWN_EXECUTION_ERROR = "UNKNOWN_EXECUTION_ERROR"
    DISPATCHER_ERROR = "DISPATCHER_ERROR"
    TOOL_SERIALIZATION_ERROR = "TOOL_SERIALIZATION_ERROR"
    A3_QUARANTINED_OUTPUT_INVALID = "A3_QUARANTINED_OUTPUT_INVALID"
    A3_QUARANTINED_POSTCONDITION_FAILED = "A3_QUARANTINED_POSTCONDITION_FAILED"


class ValidationDecision(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"


class DispatchStatus(str, Enum):
    NOT_DISPATCHED = "not_dispatched"
    SUCCESS = "success"
    NATIVE_ERROR = "native_error"
    DISPATCHER_ERROR = "dispatcher_error"
    SERIALIZATION_ERROR = "serialization_error"


class MaterializationOrigin(str, Enum):
    """Provenance of the visible ToolMessage, not a dispatch-parity claim."""

    NATIVE_DISPATCH = "native_dispatch"
    HARNESS_REJECT = "harness_reject"


class ToolEventPhase(str, Enum):
    PROPOSED = "proposed"
    PARSED = "parsed"
    VALIDATED = "validated"
    DISPATCH_STARTED = "dispatch_started"
    DISPATCH_COMPLETED = "dispatch_completed"
    RESULT_EMITTED = "result_emitted"
    QUARANTINED = "quarantined"


class StateHashScope(str, Enum):
    FULL_DB = "full_db"


class SchemaValidationPolicy(ContractModel):
    """Per-call policy; it never mutates a native Tool.params model."""

    contract_version: Literal["schema-validation-policy/v1"] = (
        "schema-validation-policy/v1"
    )
    strict: Literal[True] = True
    extra: Literal["forbid"] = "forbid"
    mutate_native_params_model: Literal[False] = False


STRICT_SCHEMA_POLICY = SchemaValidationPolicy()


def _validate_json_value(value: Any) -> None:
    """Reject non-JSON and non-finite values before canonical serialization."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON forbids non-finite float values")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            _validate_json_value(item)
        return
    raise TypeError(f"canonical JSON does not support {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the v1 canonical JSON byte representation.

    Objects are recursively key-sorted by Python's Unicode string ordering.
    Lists remain ordered sequences: they are never sorted, deduplicated or
    treated as sets. UTF-8 is used directly; non-finite floats and non-JSON
    values are rejected.
    """
    _validate_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def synthetic_parser_rejection_id(
    *,
    run_id: str,
    turn_index: int,
    malformed_call_ordinal: int,
    provider_call_id: str | None,
    requestor: Requestor,
    raw_tool_name: str | None,
    raw_arguments_text: str | None,
) -> str:
    """Create collision-resistant deterministic parser-rejection ID.

    The ordinal is the zero-based appearance order among malformed calls in the
    same model response. It differentiates identical malformed calls in one
    turn without relying on timestamps or random UUIDs.
    """
    if not run_id:
        raise ValueError("run_id must be non-empty")
    if turn_index < 0 or malformed_call_ordinal < 0:
        raise ValueError("turn_index and malformed_call_ordinal must be non-negative")
    parts = [
        "toolcall-parse-v2",
        run_id,
        str(turn_index),
        str(malformed_call_ordinal),
        provider_call_id or "",
        requestor.value,
        raw_tool_name or "",
        raw_arguments_text or "",
    ]
    return "hparse_" + hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]


def _nonempty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(CONTRACT_TIMEZONE_ERROR)
    return value


class ToolCallRequest(ContractModel):
    """Immutable proposed call before A1 validation or native dispatch."""

    contract_version: Literal["tool-call-request/v1"] = "tool-call-request/v1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    logical_call_id: str = Field(min_length=1)
    provider_call_id: Optional[str] = None
    source_kind: RequestSourceKind
    requestor: Requestor
    tool_name: Optional[str] = None
    raw_arguments_text: Optional[str] = None
    arguments_json: Optional[str] = None
    malformed_call_ordinal: Optional[int] = Field(default=None, ge=0)
    parse_status: ParseStatus
    parse_error_code: Optional[HarnessErrorCode] = None
    request_sha256: str = ""
    created_at: datetime

    @field_validator("run_id", "task_id", "logical_call_id")
    @classmethod
    def nonempty_identity(cls, value: str, info):
        return _nonempty(value, info.field_name)

    @field_validator("tool_name")
    @classmethod
    def nonempty_optional_tool_name(cls, value: Optional[str]):
        if value is not None:
            return _nonempty(value, "tool_name")
        return value

    @field_validator("created_at")
    @classmethod
    def timezone_aware_created_at(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_parse_contract(self):
        parse_codes = {
            HarnessErrorCode.PARSE_MALFORMED_JSON,
            HarnessErrorCode.PARSE_ARGUMENTS_NOT_OBJECT,
            HarnessErrorCode.PARSE_RESPONSE_STRUCTURE_INVALID,
            HarnessErrorCode.PARSE_REQUESTOR_METADATA_INVALID,
        }
        if self.parse_status == ParseStatus.PARSED:
            if self.source_kind != RequestSourceKind.PARSED_PROVIDER_CALL:
                raise ValueError("parsed request must use parsed_provider_call source")
            if self.tool_name is None or self.arguments_json is None:
                raise ValueError("parsed request requires tool_name and arguments_json")
            try:
                arguments = json.loads(self.arguments_json)
            except json.JSONDecodeError as exc:
                raise ValueError("arguments_json must be valid JSON") from exc
            if not isinstance(arguments, dict):
                raise ValueError("arguments_json must encode an object")
            if canonical_json_bytes(arguments).decode("utf-8") != self.arguments_json:
                raise ValueError("arguments_json must use canonical JSON serialization")
            if self.parse_error_code is not None:
                raise ValueError("parsed request must not carry parse_error_code")
            if self.malformed_call_ordinal is not None:
                raise ValueError("parsed request must not carry malformed_call_ordinal")
        else:
            if self.source_kind != RequestSourceKind.SYNTHETIC_PARSER_REJECTION:
                raise ValueError("rejected parse must use synthetic_parser_rejection source")
            if self.arguments_json is not None:
                raise ValueError("synthetic parser rejection must not carry arguments_json")
            if self.parse_error_code not in parse_codes:
                raise ValueError("synthetic parser rejection requires a parse error code")
            if not self.logical_call_id.startswith("hparse_"):
                raise ValueError("synthetic parser rejection ID must start with hparse_")
            if self.malformed_call_ordinal is None:
                raise ValueError("synthetic parser rejection requires malformed_call_ordinal")
            expected_id = synthetic_parser_rejection_id(
                run_id=self.run_id,
                turn_index=self.turn_index,
                malformed_call_ordinal=self.malformed_call_ordinal,
                provider_call_id=self.provider_call_id,
                requestor=self.requestor,
                raw_tool_name=self.tool_name,
                raw_arguments_text=self.raw_arguments_text,
            )
            if self.logical_call_id != expected_id:
                raise ValueError("synthetic parser rejection ID is not canonical")
        expected = canonical_json_sha256(
            {
                "contract_version": self.contract_version,
                "run_id": self.run_id,
                "task_id": self.task_id,
                "trial_index": self.trial_index,
                "trial_seed": self.trial_seed,
                "turn_index": self.turn_index,
                "logical_call_id": self.logical_call_id,
                "provider_call_id": self.provider_call_id,
                "source_kind": self.source_kind.value,
                "requestor": self.requestor.value,
                "tool_name": self.tool_name,
                "raw_arguments_text": self.raw_arguments_text,
                "arguments_json": self.arguments_json,
                "malformed_call_ordinal": self.malformed_call_ordinal,
                "parse_status": self.parse_status.value,
                "parse_error_code": self.parse_error_code.value
                if self.parse_error_code
                else None,
            }
        )
        if self.request_sha256 and self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match immutable request fields")
        object.__setattr__(self, "request_sha256", expected)
        return self

    @classmethod
    def parsed(
        cls,
        *,
        run_id: str,
        task_id: str,
        trial_index: int,
        trial_seed: int,
        turn_index: int,
        logical_call_id: str,
        requestor: Requestor,
        tool_name: str,
        arguments: Mapping[str, Any],
        provider_call_id: str | None,
        raw_arguments_text: str | None,
        created_at: datetime,
    ) -> "ToolCallRequest":
        return cls(
            run_id=run_id,
            task_id=task_id,
            trial_index=trial_index,
            trial_seed=trial_seed,
            turn_index=turn_index,
            logical_call_id=logical_call_id,
            provider_call_id=provider_call_id,
            source_kind=RequestSourceKind.PARSED_PROVIDER_CALL,
            requestor=requestor,
            tool_name=tool_name,
            raw_arguments_text=raw_arguments_text,
            arguments_json=canonical_json_bytes(dict(arguments)).decode("utf-8"),
            malformed_call_ordinal=None,
            parse_status=ParseStatus.PARSED,
            created_at=created_at,
        )

    @classmethod
    def parser_rejection(
        cls,
        *,
        run_id: str,
        task_id: str,
        trial_index: int,
        trial_seed: int,
        turn_index: int,
        malformed_call_ordinal: int,
        requestor: Requestor,
        error_code: HarnessErrorCode,
        provider_call_id: str | None,
        raw_tool_name: str | None,
        raw_arguments_text: str | None,
        created_at: datetime,
    ) -> "ToolCallRequest":
        return cls(
            run_id=run_id,
            task_id=task_id,
            trial_index=trial_index,
            trial_seed=trial_seed,
            turn_index=turn_index,
            logical_call_id=synthetic_parser_rejection_id(
                run_id=run_id,
                turn_index=turn_index,
                malformed_call_ordinal=malformed_call_ordinal,
                provider_call_id=provider_call_id,
                requestor=requestor,
                raw_tool_name=raw_tool_name,
                raw_arguments_text=raw_arguments_text,
            ),
            provider_call_id=provider_call_id,
            source_kind=RequestSourceKind.SYNTHETIC_PARSER_REJECTION,
            requestor=requestor,
            tool_name=raw_tool_name,
            raw_arguments_text=raw_arguments_text,
            malformed_call_ordinal=malformed_call_ordinal,
            parse_status=ParseStatus.REJECTED,
            parse_error_code=error_code,
            created_at=created_at,
        )


class ValidationViolation(ContractModel):
    code: HarnessErrorCode
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    constraint_id: Optional[str] = None
    metadata_json: str = "{}"

    @model_validator(mode="after")
    def canonical_metadata(self):
        try:
            parsed = json.loads(self.metadata_json)
        except json.JSONDecodeError as exc:
            raise ValueError("metadata_json must be valid JSON") from exc
        canonical = canonical_json_bytes(parsed).decode("utf-8")
        if canonical != self.metadata_json:
            raise ValueError("metadata_json must use canonical JSON serialization")
        return self


class ValidationResult(ContractModel):
    contract_version: Literal["validation-result/v1"] = "validation-result/v1"
    logical_call_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    decision: ValidationDecision
    failed_stage: Optional[HarnessStage] = None
    failure_code: Optional[HarnessErrorCode] = None
    violations: tuple[ValidationViolation, ...] = ()
    schema_policy: SchemaValidationPolicy = STRICT_SCHEMA_POLICY
    tool_schema_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    domain_constraints_sha256: Optional[str] = Field(
        default=None, pattern=SHA256_PATTERN
    )
    passed_stages: tuple[HarnessStage, ...] = ()
    arguments_unchanged: Literal[True] = True
    dispatch_attempted: Literal[False] = False
    dispatch_count: Literal[0] = 0
    validated_at: datetime

    @field_validator("logical_call_id")
    @classmethod
    def nonempty_call_id(cls, value: str):
        return _nonempty(value, "logical_call_id")

    @field_validator("validated_at")
    @classmethod
    def timezone_aware_validated_at(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_decision_contract(self):
        if self.decision == ValidationDecision.ALLOW:
            if self.failed_stage is not None or self.failure_code is not None or self.violations:
                raise ValueError("allow result cannot carry failure metadata")
            if not self.passed_stages:
                raise ValueError("allow result must record passed validation stages")
        else:
            if (
                self.failed_stage is None
                or self.failure_code is None
                or not self.violations
            ):
                raise ValueError("reject result requires stage, code and violation")
            if self.failure_code not in {item.code for item in self.violations}:
                raise ValueError("failure_code must appear in violations")
        if len(self.passed_stages) != len(set(self.passed_stages)):
            raise ValueError("passed_stages cannot contain duplicates")
        return self


class HarnessError(ContractModel):
    contract_version: Literal["harness-error/v1"] = "harness-error/v1"
    error_id: str = Field(min_length=1)
    logical_call_id: str = Field(min_length=1)
    stage: HarnessStage
    code: HarnessErrorCode
    message: str = Field(min_length=1)
    details_json: str = "{}"
    retryable: Literal[False] = False
    repairable: Literal[False] = False
    cause_type: Optional[str] = None
    cause_message_redacted: Optional[str] = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timezone_aware_error_time(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def canonical_details(self):
        try:
            parsed = json.loads(self.details_json)
        except json.JSONDecodeError as exc:
            raise ValueError("details_json must be valid JSON") from exc
        canonical = canonical_json_bytes(parsed).decode("utf-8")
        if canonical != self.details_json:
            raise ValueError("details_json must use canonical JSON serialization")
        return self


class ToolMessageRecord(ContractModel):
    """Contract representation of native-compatible ToolMessage fields."""

    id: str = Field(min_length=1)
    role: Literal["tool"] = "tool"
    requestor: Requestor
    content: Optional[str] = None
    error: bool


class StateHashSnapshot(ContractModel):
    """Full-DB canonical state hash for deterministic A1 WRITE tests only."""

    contract_version: Literal["state-hash/v1"] = "state-hash/v1"
    scope: Literal[StateHashScope.FULL_DB] = StateHashScope.FULL_DB
    canonicalization_version: Literal["canonical-json/v1"] = "canonical-json/v1"
    sha256: str = Field(pattern=SHA256_PATTERN)
    volatile_paths: tuple[()] = ()

    @classmethod
    def from_full_db_state(cls, state: Any) -> "StateHashSnapshot":
        if isinstance(state, BaseModel):
            state = state.model_dump(mode="json")
        return cls(sha256=canonical_json_sha256(state))


class DispatchResult(ContractModel):
    contract_version: Literal["dispatch-result/v1"] = "dispatch-result/v1"
    logical_call_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_decision: ValidationDecision
    attempt_index: Literal[0] = 0
    dispatch_attempted: bool
    dispatch_count: int = Field(ge=0, le=1)
    status: DispatchStatus
    origin: MaterializationOrigin
    tool_message: ToolMessageRecord
    harness_error: Optional[HarnessError] = None
    started_at: Optional[datetime] = None
    completed_at: datetime
    latency_ms: Optional[int] = Field(default=None, ge=0)
    state_before: Optional[StateHashSnapshot] = None
    state_after: Optional[StateHashSnapshot] = None

    @field_validator("completed_at", "started_at")
    @classmethod
    def timezone_aware_dispatch_times(cls, value: Optional[datetime]):
        if value is None:
            return value
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_dispatch_contract(self):
        if self.validation_decision == ValidationDecision.REJECT:
            if self.dispatch_attempted or self.dispatch_count != 0:
                raise ValueError("reject must have dispatch_count=0 and no dispatch")
            if self.status != DispatchStatus.NOT_DISPATCHED:
                raise ValueError("reject must have not_dispatched status")
            if self.harness_error is None or not self.tool_message.error:
                raise ValueError("reject requires HarnessError and error ToolMessage")
            if self.origin != MaterializationOrigin.HARNESS_REJECT:
                raise ValueError("reject must have harness_reject origin")
            if self.state_before is not None and self.state_after is not None:
                if self.state_before.sha256 != self.state_after.sha256:
                    raise ValueError("rejected deterministic WRITE must not change state hash")
        else:
            if not self.dispatch_attempted or self.dispatch_count != 1:
                raise ValueError("allow must dispatch exactly once")
            if self.status == DispatchStatus.NOT_DISPATCHED:
                raise ValueError("allow cannot have not_dispatched status")
            if self.origin != MaterializationOrigin.NATIVE_DISPATCH:
                raise ValueError("allowed dispatch must have native_dispatch origin")
        if self.started_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


class ToolEvent(ContractModel):
    contract_version: Literal["tool-event/v1"] = "tool-event/v1"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    logical_call_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    phase: ToolEventPhase
    attempt_index: Literal[0] = 0
    requestor: Requestor
    tool_name: Optional[str] = None
    native_tool_type: Optional[Literal["read", "write", "think", "generic"]] = None
    origin: Optional[MaterializationOrigin] = None
    decision: Optional[ValidationDecision] = None
    failure_code: Optional[HarnessErrorCode] = None
    dispatch_status: Optional[DispatchStatus] = None
    dispatch_count: Optional[int] = Field(default=None, ge=0, le=1)
    state_hash_before: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    state_hash_after: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    exception_type: Optional[str] = None
    exception_message_redacted: Optional[str] = None
    raw_retention: Literal["none", "fixture"] = "none"
    raw_payload_json: Optional[str] = None
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_ref: Optional[str] = None
    timestamp: datetime
    telemetry_schema_version: Literal["tool-event/v1"] = "tool-event/v1"

    @field_validator("timestamp")
    @classmethod
    def timezone_aware_event_time(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_event_phase(self):
        if self.raw_retention == "none" and self.raw_payload_json is not None:
            raise ValueError("raw_payload_json requires fixture raw retention")
        if self.raw_payload_json is not None:
            try:
                raw_payload = json.loads(self.raw_payload_json)
            except json.JSONDecodeError as exc:
                raise ValueError("raw_payload_json must be valid JSON") from exc
            if canonical_json_bytes(raw_payload).decode("utf-8") != self.raw_payload_json:
                raise ValueError("raw_payload_json must use canonical JSON serialization")
        if self.phase == ToolEventPhase.VALIDATED and self.decision is None:
            raise ValueError("validated event requires decision")
        if self.phase in {
            ToolEventPhase.DISPATCH_STARTED,
            ToolEventPhase.DISPATCH_COMPLETED,
            ToolEventPhase.QUARANTINED,
        } and self.decision != ValidationDecision.ALLOW:
            raise ValueError("dispatch events require allow decision")
        if self.phase == ToolEventPhase.QUARANTINED and self.failure_code not in {
            HarnessErrorCode.A3_QUARANTINED_OUTPUT_INVALID,
            HarnessErrorCode.A3_QUARANTINED_POSTCONDITION_FAILED,
        }:
            raise ValueError("quarantined event requires an A3 quarantine code")
        if self.phase == ToolEventPhase.RESULT_EMITTED and self.decision is None:
            raise ValueError("result_emitted event requires decision")
        return self

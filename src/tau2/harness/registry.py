"""HAR-02 immutable native-tool registry snapshot and validation.

This module reads native toolkit metadata to construct a frozen snapshot.  It
never invokes a Tool, Environment, dispatcher, model adapter, or gateway.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from tau2.environment.toolkit import ToolKitBase
from tau2.harness.contracts import (
    ContractModel,
    HarnessErrorCode,
    HarnessStage,
    Requestor,
    TrustedRouteContext,
    ToolCallRequest,
    _require_timezone,
    canonical_json_bytes,
    canonical_json_sha256,
)


REGISTRY_SCHEMA_VERSION = "tool-registry/v1"


class RegistryPreflightError(ValueError):
    """Fail-closed registry construction failure; never resolve ambiguity."""

    code = HarnessErrorCode.PREFLIGHT_DUPLICATE_TOOL_NAME


class RegistryTool(ContractModel):
    """A non-executable description of one native tool in one namespace."""

    tool_name: str = Field(min_length=1)
    allowed_requestor: Requestor
    toolkit_identity: str = Field(min_length=1)
    params_model_identity: str = Field(min_length=1)
    params_schema_json: str = Field(min_length=2)
    params_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_tool_type: Optional[Literal["read", "write", "think", "generic"]] = None

    @model_validator(mode="after")
    def validate_schema_identity(self):
        try:
            decoded = json.loads(self.params_schema_json)
            encoded = canonical_json_bytes(decoded)
        except Exception as exc:
            raise ValueError("params_schema_json must be canonical JSON") from exc
        if encoded.decode("utf-8") != self.params_schema_json:
            raise ValueError("params_schema_json must use canonical JSON serialization")
        if canonical_json_sha256(decoded) != self.params_schema_sha256:
            raise ValueError("params_schema_sha256 does not match params_schema_json")
        return self

    def canonical_record(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "allowed_requestor": self.allowed_requestor.value,
            "toolkit_identity": self.toolkit_identity,
            "params_model_identity": self.params_model_identity,
            "params_schema_json": self.params_schema_json,
            "params_schema_sha256": self.params_schema_sha256,
            "native_tool_type": self.native_tool_type,
        }


class RegistrySnapshot(ContractModel):
    """Immutable, insertion-order-independent registry evidence."""

    contract_version: Literal["tool-registry/v1"] = REGISTRY_SCHEMA_VERSION
    entries: tuple[RegistryTool, ...]
    registry_sha256: str = Field(default="", pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot(self):
        key = lambda item: (
            item.allowed_requestor.value,
            item.tool_name,
            item.toolkit_identity,
        )
        if tuple(sorted(self.entries, key=key)) != self.entries:
            raise ValueError("registry entries must be in canonical sorted order")
        namespace_keys = [(item.allowed_requestor.value, item.tool_name) for item in self.entries]
        if len(namespace_keys) != len(set(namespace_keys)):
            raise ValueError("duplicate tool name in one requestor namespace")
        expected = canonical_json_sha256(
            {
                "contract_version": self.contract_version,
                "entries": [entry.canonical_record() for entry in self.entries],
            }
        )
        if self.registry_sha256 and self.registry_sha256 != expected:
            raise ValueError("registry_sha256 does not match canonical registry")
        object.__setattr__(self, "registry_sha256", expected)
        return self

    def find(self, requestor: Requestor, tool_name: str) -> RegistryTool | None:
        for entry in self.entries:
            if entry.allowed_requestor == requestor and entry.tool_name == tool_name:
                return entry
        return None

    def exists_for_other_requestor(self, requestor: Requestor, tool_name: str) -> bool:
        return any(
            entry.allowed_requestor != requestor and entry.tool_name == tool_name
            for entry in self.entries
        )


def _native_toolkit_identity(toolkit: ToolKitBase) -> str:
    return f"{type(toolkit).__module__}:{type(toolkit).__qualname__}"


def _params_model_identity(params_model: type) -> str:
    return f"{params_model.__module__}:{params_model.__qualname__}"


def build_registry_snapshot(
    namespaces: Iterable[tuple[Requestor, ToolKitBase]],
) -> RegistrySnapshot:
    """Read native tool metadata and construct a deterministic snapshot.

    Duplicate ``(requestor, tool_name)`` pairs deliberately raise before any
    snapshot is returned. This prevents last-write-wins behavior.
    """
    entries: list[RegistryTool] = []
    seen: set[tuple[Requestor, str]] = set()
    for requestor, toolkit in namespaces:
        toolkit_identity = _native_toolkit_identity(toolkit)
        for tool_name, tool in toolkit.get_tools().items():
            key = (requestor, tool_name)
            if key in seen:
                raise RegistryPreflightError(
                    f"{HarnessErrorCode.PREFLIGHT_DUPLICATE_TOOL_NAME.value}: "
                    f"{requestor.value}/{tool_name}"
                )
            seen.add(key)
            schema = tool.params.model_json_schema()
            schema_json = canonical_json_bytes(schema).decode("utf-8")
            native_type = toolkit.tool_type(tool_name).value
            entries.append(
                RegistryTool(
                    tool_name=tool_name,
                    allowed_requestor=requestor,
                    toolkit_identity=toolkit_identity,
                    params_model_identity=_params_model_identity(tool.params),
                    params_schema_json=schema_json,
                    params_schema_sha256=canonical_json_sha256(schema),
                    native_tool_type=native_type,
                )
            )
    return RegistrySnapshot(
        entries=tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.allowed_requestor.value,
                    item.tool_name,
                    item.toolkit_identity,
                ),
            )
        )
    )


class RegistryValidationResult(ContractModel):
    """Non-dispatching result of requestor plus registry validation."""

    contract_version: Literal["registry-validation-result/v1"] = (
        "registry-validation-result/v1"
    )
    logical_call_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed: bool
    passed_stages: tuple[HarnessStage, ...] = ()
    failed_stage: Optional[HarnessStage] = None
    failure_code: Optional[HarnessErrorCode] = None
    resolved_tool: Optional[RegistryTool] = None
    validated_at: datetime

    @field_validator("validated_at")
    @classmethod
    def timezone_aware_validated_at(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.allowed:
            if self.failed_stage is not None or self.failure_code is not None:
                raise ValueError("allowed registry result cannot carry failure metadata")
            if self.passed_stages != (HarnessStage.REQUESTOR, HarnessStage.REGISTRY):
                raise ValueError("allowed registry result must pass requestor then registry")
            if self.resolved_tool is None:
                raise ValueError("allowed registry result requires resolved_tool")
        else:
            if self.failed_stage not in {HarnessStage.REQUESTOR, HarnessStage.REGISTRY}:
                raise ValueError("rejected registry result requires requestor or registry stage")
            if self.failure_code not in {
                HarnessErrorCode.REGISTRY_UNKNOWN_TOOL,
                HarnessErrorCode.REGISTRY_WRONG_REQUESTOR,
            }:
                raise ValueError("rejected registry result requires registry failure code")
            if self.resolved_tool is not None:
                raise ValueError("rejected registry result cannot resolve a tool")
        return self


class RequestorValidator:
    """Validate parsed requestor against caller-owned trusted route context."""

    @staticmethod
    def matches(request: ToolCallRequest, route: TrustedRouteContext) -> bool:
        return request.requestor == route.requestor


class RegistryValidator:
    """Validate only snapshot membership and requestor authorization."""

    def __init__(self, snapshot: RegistrySnapshot):
        self._snapshot = snapshot

    @property
    def snapshot(self) -> RegistrySnapshot:
        return self._snapshot

    def validate(
        self,
        request: ToolCallRequest,
        *,
        trusted_route: TrustedRouteContext,
        validated_at: datetime,
    ) -> RegistryValidationResult:
        if request.tool_name is None:
            raise ValueError("RegistryValidator requires a parsed ToolCallRequest with tool_name")
        if not RequestorValidator.matches(request, trusted_route):
            return RegistryValidationResult(
                logical_call_id=request.logical_call_id,
                request_sha256=request.request_sha256,
                registry_sha256=self._snapshot.registry_sha256,
                allowed=False,
                failed_stage=HarnessStage.REQUESTOR,
                failure_code=HarnessErrorCode.REGISTRY_WRONG_REQUESTOR,
                validated_at=validated_at,
            )
        resolved = self._snapshot.find(request.requestor, request.tool_name)
        if resolved is not None:
            return RegistryValidationResult(
                logical_call_id=request.logical_call_id,
                request_sha256=request.request_sha256,
                registry_sha256=self._snapshot.registry_sha256,
                allowed=True,
                passed_stages=(HarnessStage.REQUESTOR, HarnessStage.REGISTRY),
                resolved_tool=resolved,
                validated_at=validated_at,
            )
        code = (
            HarnessErrorCode.REGISTRY_WRONG_REQUESTOR
            if self._snapshot.exists_for_other_requestor(request.requestor, request.tool_name)
            else HarnessErrorCode.REGISTRY_UNKNOWN_TOOL
        )
        return RegistryValidationResult(
            logical_call_id=request.logical_call_id,
            request_sha256=request.request_sha256,
            registry_sha256=self._snapshot.registry_sha256,
            allowed=False,
            passed_stages=(HarnessStage.REQUESTOR,),
            failed_stage=HarnessStage.REGISTRY,
            failure_code=code,
            validated_at=validated_at,
        )

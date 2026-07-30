"""HAR-04 standalone strict schema and approved domain-constraint validation.

This module validates already parsed requests against an already resolved
registry entry. It does not look up a registry, create ToolMessage objects,
dispatch a tool, access an Environment, or call an LLM/gateway.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from tau2.harness.contracts import (
    HarnessErrorCode,
    HarnessStage,
    ToolCallRequest,
    ValidationDecision,
    ValidationResult,
    ValidationViolation,
    canonical_json_bytes,
    canonical_json_sha256,
)
from tau2.harness.registry import RegistryTool


APPROVED_DOMAIN_CONSTRAINTS = {
    "version": "approved-domain-constraints/v1",
    "rules": [
        {
            "id": "retail.modify_pending_order_items.item_ids_ne_new_item_ids",
            "tool_name": "modify_pending_order_items",
            "rule": "for each aligned index, item_ids[i] != new_item_ids[i]",
        }
    ],
}
APPROVED_DOMAIN_CONSTRAINTS_SHA256 = canonical_json_sha256(APPROVED_DOMAIN_CONSTRAINTS)


def _params_model_identity(params_model: Type[BaseModel]) -> str:
    return f"{params_model.__module__}:{params_model.__qualname__}"


def _schema_identity_matches(resolved_tool: RegistryTool, params_model: Type[BaseModel]) -> bool:
    schema = params_model.model_json_schema()
    return (
        resolved_tool.params_model_identity == _params_model_identity(params_model)
        and resolved_tool.params_schema_sha256 == canonical_json_sha256(schema)
        and resolved_tool.params_schema_json == canonical_json_bytes(schema).decode("utf-8")
    )


def _path_from_error(error: dict[str, Any]) -> str:
    location = error.get("loc", ())
    if not location:
        return "$"
    return ".".join(str(part) for part in location)


def _schema_code(error_type: str) -> HarnessErrorCode:
    if error_type == "missing":
        return HarnessErrorCode.SCHEMA_MISSING_REQUIRED_ARG
    if error_type == "extra_forbidden":
        return HarnessErrorCode.SCHEMA_EXTRA_ARGUMENT
    strict_type_errors = {
        "string_type", "list_type", "dict_type", "model_type", "int_type",
        "float_type", "bool_type", "bytes_type", "tuple_type", "set_type",
        "frozen_set_type", "none_required", "is_instance_of", "is_subclass_of",
        "string_sub_type",
    }
    if error_type in strict_type_errors:
        return HarnessErrorCode.SCHEMA_WRONG_ARG_TYPE
    return HarnessErrorCode.SCHEMA_VALIDATION_ERROR


def _schema_violations(error: ValidationError) -> tuple[ValidationViolation, ...]:
    violations: list[ValidationViolation] = []
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        error_type = str(item.get("type", "unknown"))
        violations.append(
            ValidationViolation(
                code=_schema_code(error_type),
                path=_path_from_error(item),
                message=str(item.get("msg", "Schema validation failed.")),
                metadata_json=canonical_json_bytes({"pydantic_error_type": error_type}).decode("utf-8"),
            )
        )
    return tuple(violations)


class StrictSchemaDomainValidator:
    """Strict Pydantic validation plus the sole approved Retail rule."""

    @staticmethod
    def validate(
        request: ToolCallRequest,
        resolved_tool: RegistryTool,
        *,
        params_model: Type[BaseModel],
        validated_at: datetime,
    ) -> ValidationResult:
        if request.tool_name is None or request.arguments_json is None:
            raise ValueError("HAR-04 requires a successfully parsed ToolCallRequest")
        if request.tool_name != resolved_tool.tool_name:
            raise ValueError("resolved RegistryTool does not match ToolCallRequest tool_name")
        if not _schema_identity_matches(resolved_tool, params_model):
            raise ValueError("native params model does not match resolved RegistryTool snapshot")

        # Decode canonical request data into a fresh dict. It is never edited,
        # and the model output is discarded so defaults/coercion never alter
        # semantics supplied to a later dispatcher.
        arguments = json.loads(request.arguments_json)
        try:
            params_model.model_validate(arguments, strict=True, extra="forbid")
        except ValidationError as exc:
            violations = _schema_violations(exc)
            return ValidationResult(
                logical_call_id=request.logical_call_id,
                request_sha256=request.request_sha256,
                decision=ValidationDecision.REJECT,
                failed_stage=HarnessStage.SCHEMA,
                failure_code=violations[0].code,
                violations=violations,
                tool_schema_sha256=resolved_tool.params_schema_sha256,
                domain_constraints_sha256=APPROVED_DOMAIN_CONSTRAINTS_SHA256,
                passed_stages=(),
                validated_at=validated_at,
            )

        domain_violation = StrictSchemaDomainValidator._approved_domain_violation(
            request.tool_name, arguments
        )
        if domain_violation is not None:
            return ValidationResult(
                logical_call_id=request.logical_call_id,
                request_sha256=request.request_sha256,
                decision=ValidationDecision.REJECT,
                failed_stage=HarnessStage.DOMAIN_CONSTRAINT,
                failure_code=domain_violation.code,
                violations=(domain_violation,),
                tool_schema_sha256=resolved_tool.params_schema_sha256,
                domain_constraints_sha256=APPROVED_DOMAIN_CONSTRAINTS_SHA256,
                passed_stages=(HarnessStage.SCHEMA,),
                validated_at=validated_at,
            )
        return ValidationResult(
            logical_call_id=request.logical_call_id,
            request_sha256=request.request_sha256,
            decision=ValidationDecision.ALLOW,
            tool_schema_sha256=resolved_tool.params_schema_sha256,
            domain_constraints_sha256=APPROVED_DOMAIN_CONSTRAINTS_SHA256,
            passed_stages=(HarnessStage.SCHEMA, HarnessStage.DOMAIN_CONSTRAINT),
            validated_at=validated_at,
        )

    @staticmethod
    def _approved_domain_violation(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ValidationViolation | None:
        if tool_name != "modify_pending_order_items":
            return None
        # The native signature uses list fields, not old_item_id/new_item_id.
        # Deliberately use zip: length/product/order-status checks are outside
        # this single approved rule.
        for index, (old_item_id, new_item_id) in enumerate(
            zip(arguments["item_ids"], arguments["new_item_ids"])
        ):
            if old_item_id == new_item_id:
                return ValidationViolation(
                    code=HarnessErrorCode.DOMAIN_CONSTRAINT_VIOLATION,
                    path=f"item_ids.{index}",
                    message="old_item_id must differ from new_item_id at the same index.",
                    constraint_id="retail.modify_pending_order_items.item_ids_ne_new_item_ids",
                    metadata_json=canonical_json_bytes({"index": index}).decode("utf-8"),
                )
        return None

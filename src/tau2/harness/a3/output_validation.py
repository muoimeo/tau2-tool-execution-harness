"""A3 output-contract checks before successful ToolMessage materialization."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from tau2.harness.a3.contracts import OutputValidationCode, OutputValidationResult


def validate_output(raw_result: Any, *, returns_model: type[BaseModel]) -> OutputValidationResult:
    annotation = returns_model.model_fields["returns"].annotation
    expected = getattr(annotation, "__qualname__", str(annotation))
    if raw_result is None:
        return OutputValidationResult(valid=False, code=OutputValidationCode.OUTPUT_NONE, expected_type=expected)
    try:
        returns_model.model_validate({"returns": raw_result}, strict=True, extra="forbid")
    except ValidationError as exc:
        code = (OutputValidationCode.OUTPUT_REQUIRED_FIELD_MISSING
                if any(item.get("type") == "missing" for item in exc.errors())
                else OutputValidationCode.OUTPUT_WRONG_TYPE)
        return OutputValidationResult(valid=False, code=code, expected_type=expected)
    return OutputValidationResult(valid=True, expected_type=expected)

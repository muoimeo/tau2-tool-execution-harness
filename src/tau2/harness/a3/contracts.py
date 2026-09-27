"""Immutable A3 recovery contracts.

A3 records deterministic representation repair and post-dispatch review.  It
does not model a retry, rollback, or semantic re-plan.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from tau2.harness.a1.contracts import ContractModel


class RepairType(str, Enum):
    MARKDOWN_CODE_FENCE = "markdown_code_fence"
    DOUBLE_ENCODED_JSON_OBJECT = "double_encoded_json_object"
    TRAILING_COMMA = "trailing_comma"


class RecoveryDisposition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    REPAIRED_DISPATCHED = "repaired_dispatched"
    UNREPAIRABLE_REJECTED = "unrepairable_rejected"
    OUTPUT_VALID = "output_valid"
    QUARANTINED_OUTPUT_INVALID = "quarantined_output_invalid"
    QUARANTINED_POSTCONDITION_FAILED = "quarantined_postcondition_failed"


class OutputValidationCode(str, Enum):
    OUTPUT_NONE = "A3_OUTPUT_NONE"
    OUTPUT_WRONG_TYPE = "A3_OUTPUT_WRONG_TYPE"
    OUTPUT_REQUIRED_FIELD_MISSING = "A3_OUTPUT_REQUIRED_FIELD_MISSING"
    OUTPUT_SERIALIZATION_FAILED = "A3_OUTPUT_SERIALIZATION_FAILED"


class RepairCandidate(ContractModel):
    repair_type: RepairType
    original_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repaired_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repaired_payload: str = Field(min_length=1, exclude=True)


class OutputValidationResult(ContractModel):
    valid: bool
    code: OutputValidationCode | None = None
    expected_type: str | None = None


class PostconditionResult(ContractModel):
    applicable: bool
    passed: bool | None = None
    rule_id: str | None = None
    pre_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    post_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RecoveryAuditEvent(ContractModel):
    contract_version: Literal["a3-recovery-event/v1"] = "a3-recovery-event/v1"
    sequence: int = Field(ge=1)
    logical_call_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: RecoveryDisposition
    original_failure: str | None = None
    repair_attempt_count: int = Field(ge=0, le=1)
    repair_type: RepairType | None = None
    original_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    repaired_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validation_outcome: str | None = None
    dispatch_count: int = Field(ge=0, le=1)
    write_count: int = Field(ge=0, le=1)
    output_validation: OutputValidationResult | None = None
    postcondition: PostconditionResult | None = None
    quarantine_reason: str | None = None

"""HAR-03 standalone tool-call argument parser and normalizer.

This module does no registry lookup, schema validation, ToolMessage creation,
tool dispatch, environment access, model generation, or gateway I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, Optional, Sequence

from pydantic import Field, field_validator, model_validator

from tau2.harness.a1.contracts import (
    ContractModel,
    HarnessErrorCode,
    Requestor,
    ToolCallRequest,
    TrustedRouteContext,
    _require_timezone,
    canonical_json_sha256,
    synthetic_parser_rejection_id,
)


class RawToolCall(ContractModel):
    """Unparsed provider tool-call fields retained exactly as received."""

    provider_call_id: Optional[str] = None
    raw_tool_name: Optional[str] = None
    raw_arguments_text: Optional[str] = None


class ParserFailure(ContractModel):
    """A parse rejection, deliberately not a ToolCallRequest."""

    contract_version: Literal["parser-failure/v1"] = "parser-failure/v1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    raw_call_index: int = Field(ge=0)
    malformed_call_ordinal: int = Field(ge=0)
    requestor: Requestor
    raw_call: RawToolCall
    error_code: Literal[
        HarnessErrorCode.PARSE_MALFORMED_JSON,
        HarnessErrorCode.PARSE_ARGUMENTS_NOT_OBJECT,
        HarnessErrorCode.PARSE_RESPONSE_STRUCTURE_INVALID,
    ]
    synthetic_call_id: str = Field(min_length=7)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_failure_identity(self):
        expected_id = synthetic_parser_rejection_id(
            run_id=self.run_id,
            turn_index=self.turn_index,
            malformed_call_ordinal=self.malformed_call_ordinal,
            provider_call_id=self.raw_call.provider_call_id,
            requestor=self.requestor,
            raw_tool_name=self.raw_call.raw_tool_name,
            raw_arguments_text=self.raw_call.raw_arguments_text,
        )
        if self.synthetic_call_id != expected_id:
            raise ValueError("synthetic_call_id is not ARC-01A v2 canonical")
        expected_sha = canonical_json_sha256(
            {
                "contract_version": self.contract_version,
                "run_id": self.run_id,
                "task_id": self.task_id,
                "trial_index": self.trial_index,
                "trial_seed": self.trial_seed,
                "turn_index": self.turn_index,
                "raw_call_index": self.raw_call_index,
                "malformed_call_ordinal": self.malformed_call_ordinal,
                "requestor": self.requestor.value,
                "provider_call_id": self.raw_call.provider_call_id,
                "raw_tool_name": self.raw_call.raw_tool_name,
                "raw_arguments_text": self.raw_call.raw_arguments_text,
                "error_code": self.error_code.value,
            }
        )
        if self.request_sha256 != expected_sha:
            raise ValueError("request_sha256 does not match raw parser failure")
        return self

class ParserBoundaryError(ContractModel):
    """Batch-level error before parsing where no requestor can be trusted."""

    contract_version: Literal["parser-boundary-error/v1"] = "parser-boundary-error/v1"
    error_code: Literal[HarnessErrorCode.PARSE_REQUESTOR_METADATA_INVALID] = (
        HarnessErrorCode.PARSE_REQUESTOR_METADATA_INVALID
    )
    raw_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    message: str = Field(min_length=1)


class ParserBatchResult(ContractModel):
    """Immutable output of one raw provider response; it contains no ToolMessage."""

    contract_version: Literal["parser-batch-result/v1"] = "parser-batch-result/v1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trial_index: int = Field(ge=0)
    trial_seed: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    raw_calls: tuple[RawToolCall, ...]
    raw_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requestor: Optional[Requestor] = None
    requests: tuple[ToolCallRequest, ...] = ()
    failures: tuple[ParserFailure, ...] = ()
    boundary_error: Optional[ParserBoundaryError] = None
    parsed_at: datetime

    @field_validator("parsed_at")
    @classmethod
    def timezone_aware_parsed_at(cls, value: datetime):
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_batch(self):
        expected_batch_sha = canonical_json_sha256(
            {
                "raw_calls": [call.model_dump(mode="json") for call in self.raw_calls],
            }
        )
        if self.raw_batch_sha256 != expected_batch_sha:
            raise ValueError("raw_batch_sha256 does not match raw calls")
        if self.boundary_error is not None:
            if self.requestor is not None or self.requests or self.failures:
                raise ValueError("requestor boundary error must not create requests or failures")
            if self.boundary_error.raw_batch_sha256 != self.raw_batch_sha256:
                raise ValueError("boundary error must reference this raw batch")
            return self
        if self.requestor is None:
            raise ValueError("successful parser batch requires trusted requestor")
        if len(self.requests) + len(self.failures) != len(self.raw_calls):
            raise ValueError("every raw call must yield exactly one request or parser failure")
        failure_ordinals = [failure.malformed_call_ordinal for failure in self.failures]
        if failure_ordinals != list(range(len(self.failures))):
            raise ValueError("malformed call ordinals must be contiguous from zero")
        raw_indices = [failure.raw_call_index for failure in self.failures]
        if len(raw_indices) != len(set(raw_indices)):
            raise ValueError("parser failures cannot duplicate raw call index")
        # A ToolCallRequest has no raw-call-index field. Its provider ID makes
        # it traceable; order is preserved by the requests tuple itself.
        if len({failure.synthetic_call_id for failure in self.failures}) != len(self.failures):
            raise ValueError("synthetic parser IDs must be unique within a batch")
        return self


class SyntheticIdCollisionError(ValueError):
    """Fail closed when different full hashes share a truncated synthetic ID."""

    code = HarnessErrorCode.PARSE_SYNTHETIC_ID_COLLISION


class ParserNormalizer:
    """Normalize raw argument strings once, using caller-provided route context."""

    @staticmethod
    def _raw_batch_sha256(raw_calls: Sequence[RawToolCall]) -> str:
        return canonical_json_sha256(
            {"raw_calls": [call.model_dump(mode="json") for call in raw_calls]}
        )

    @staticmethod
    def _failure_request_sha256(
        *,
        run_id: str,
        task_id: str,
        trial_index: int,
        trial_seed: int,
        turn_index: int,
        raw_call_index: int,
        malformed_call_ordinal: int,
        requestor: Requestor,
        raw_call: RawToolCall,
        error_code: HarnessErrorCode,
    ) -> str:
        return canonical_json_sha256(
            {
                "contract_version": "parser-failure/v1",
                "run_id": run_id,
                "task_id": task_id,
                "trial_index": trial_index,
                "trial_seed": trial_seed,
                "turn_index": turn_index,
                "raw_call_index": raw_call_index,
                "malformed_call_ordinal": malformed_call_ordinal,
                "requestor": requestor.value,
                "provider_call_id": raw_call.provider_call_id,
                "raw_tool_name": raw_call.raw_tool_name,
                "raw_arguments_text": raw_call.raw_arguments_text,
                "error_code": error_code.value,
            }
        )

    @staticmethod
    def _trusted_requestor(route: object) -> Requestor | None:
        return route.requestor if isinstance(route, TrustedRouteContext) else None

    def parse(
        self,
        raw_calls: Sequence[RawToolCall],
        *,
        run_id: str,
        task_id: str,
        trial_index: int,
        trial_seed: int,
        turn_index: int,
        trusted_route: object,
        parsed_at: datetime,
    ) -> ParserBatchResult:
        raw_calls = tuple(raw_calls)
        batch_sha = self._raw_batch_sha256(raw_calls)
        requestor = self._trusted_requestor(trusted_route)
        if requestor is None:
            return ParserBatchResult(
                run_id=run_id, task_id=task_id, trial_index=trial_index,
                trial_seed=trial_seed, turn_index=turn_index, raw_calls=raw_calls,
                raw_batch_sha256=batch_sha, parsed_at=parsed_at,
                boundary_error=ParserBoundaryError(
                    raw_batch_sha256=batch_sha,
                    message="Trusted requestor metadata is missing or invalid.",
                ),
            )

        requests: list[ToolCallRequest] = []
        failures: list[ParserFailure] = []
        seen_synthetic_ids: dict[str, str] = {}
        malformed_ordinal = 0
        for raw_call_index, raw_call in enumerate(raw_calls):
            error_code: HarnessErrorCode | None = None
            arguments: dict | None = None
            # The raw provider argument string is decoded exactly once here,
            # including for a structurally invalid call. Structure wins if both
            # identity and argument errors are present.
            try:
                decoded = json.loads(raw_call.raw_arguments_text)
            except (json.JSONDecodeError, TypeError):
                decoded = None
                decode_error = True
            else:
                decode_error = False
            if (
                not raw_call.provider_call_id
                or not raw_call.raw_tool_name
                or not isinstance(raw_call.raw_arguments_text, str)
            ):
                error_code = HarnessErrorCode.PARSE_RESPONSE_STRUCTURE_INVALID
            elif decode_error:
                error_code = HarnessErrorCode.PARSE_MALFORMED_JSON
            elif not isinstance(decoded, dict):
                error_code = HarnessErrorCode.PARSE_ARGUMENTS_NOT_OBJECT
            else:
                arguments = decoded
            if error_code is None:
                requests.append(
                    ToolCallRequest.parsed(
                        run_id=run_id, task_id=task_id, trial_index=trial_index,
                        trial_seed=trial_seed, turn_index=turn_index,
                        logical_call_id=raw_call.provider_call_id,
                        requestor=requestor, tool_name=raw_call.raw_tool_name,
                        arguments=arguments, provider_call_id=raw_call.provider_call_id,
                        raw_arguments_text=raw_call.raw_arguments_text, created_at=parsed_at,
                    )
                )
                continue
            synthetic_id = synthetic_parser_rejection_id(
                run_id=run_id, turn_index=turn_index,
                malformed_call_ordinal=malformed_ordinal,
                provider_call_id=raw_call.provider_call_id, requestor=requestor,
                raw_tool_name=raw_call.raw_tool_name,
                raw_arguments_text=raw_call.raw_arguments_text,
            )
            failure_sha = self._failure_request_sha256(
                run_id=run_id, task_id=task_id, trial_index=trial_index,
                trial_seed=trial_seed, turn_index=turn_index,
                raw_call_index=raw_call_index, malformed_call_ordinal=malformed_ordinal,
                requestor=requestor, raw_call=raw_call, error_code=error_code,
            )
            existing = seen_synthetic_ids.get(synthetic_id)
            if existing is not None and existing != failure_sha:
                raise SyntheticIdCollisionError(
                    f"{self.code_for_collision()}: {synthetic_id} maps to multiple request hashes"
                )
            seen_synthetic_ids[synthetic_id] = failure_sha
            failures.append(
                ParserFailure(
                    run_id=run_id, task_id=task_id, trial_index=trial_index,
                    trial_seed=trial_seed, turn_index=turn_index,
                    raw_call_index=raw_call_index,
                    malformed_call_ordinal=malformed_ordinal, requestor=requestor,
                    raw_call=raw_call, error_code=error_code,
                    synthetic_call_id=synthetic_id, request_sha256=failure_sha,
                )
            )
            malformed_ordinal += 1
        return ParserBatchResult(
            run_id=run_id, task_id=task_id, trial_index=trial_index,
            trial_seed=trial_seed, turn_index=turn_index, raw_calls=raw_calls,
            raw_batch_sha256=batch_sha, requestor=requestor,
            requests=tuple(requests), failures=tuple(failures), parsed_at=parsed_at,
        )

    @staticmethod
    def code_for_collision() -> str:
        return HarnessErrorCode.PARSE_SYNTHETIC_ID_COLLISION.value

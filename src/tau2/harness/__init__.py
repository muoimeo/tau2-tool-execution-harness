"""A1 harness contracts.

This package intentionally has no runtime integration with the native tau2
parser, orchestrator, environment, or gateway.
"""

from tau2.harness.contracts import (
    DispatchResult,
    DispatchStatus,
    HarnessError,
    HarnessErrorCode,
    HarnessStage,
    Requestor,
    SchemaValidationPolicy,
    StateHashSnapshot,
    ToolCallRequest,
    ToolEvent,
    ToolEventPhase,
    TrustedRouteContext,
    ValidationDecision,
    ValidationResult,
    canonical_json_bytes,
    canonical_json_sha256,
    synthetic_parser_rejection_id,
)
from tau2.harness.parser import (
    ParserBatchResult,
    ParserBoundaryError,
    ParserFailure,
    ParserNormalizer,
    RawToolCall,
    SyntheticIdCollisionError,
)
from tau2.harness.registry import (
    RegistryPreflightError,
    RegistrySnapshot,
    RegistryTool,
    RegistryValidationResult,
    RegistryValidator,
    RequestorValidator,
    build_registry_snapshot,
)
from tau2.harness.validation import (
    APPROVED_DOMAIN_CONSTRAINTS_SHA256,
    StrictSchemaDomainValidator,
)
from tau2.harness.dispatcher import (
    A1ValidationExecutor,
    DuplicateExecutionError,
    NativeToolExecutor,
)
from tau2.harness.trace import TraceInvariantError, TraceLogger, TraceLoggerError

__all__ = [
    "DispatchResult",
    "DispatchStatus",
    "HarnessError",
    "HarnessErrorCode",
    "HarnessStage",
    "Requestor",
    "SchemaValidationPolicy",
    "StateHashSnapshot",
    "ToolCallRequest",
    "ToolEvent",
    "ToolEventPhase",
    "TrustedRouteContext",
    "ValidationDecision",
    "ValidationResult",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "synthetic_parser_rejection_id",
    "RegistryPreflightError",
    "RegistrySnapshot",
    "RegistryTool",
    "RegistryValidationResult",
    "RegistryValidator",
    "RequestorValidator",
    "build_registry_snapshot",
    "ParserBatchResult",
    "ParserBoundaryError",
    "ParserFailure",
    "ParserNormalizer",
    "RawToolCall",
    "SyntheticIdCollisionError",
    "APPROVED_DOMAIN_CONSTRAINTS_SHA256",
    "StrictSchemaDomainValidator",
    "A1ValidationExecutor",
    "DuplicateExecutionError",
    "NativeToolExecutor",
    "TraceInvariantError",
    "TraceLogger",
    "TraceLoggerError",
]

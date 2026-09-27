"""Execution harness package.

Implementation is grouped by experiment stage:

* :mod:`tau2.harness.a1` — validation, containment, tracing, and lifecycle.
* :mod:`tau2.harness.a2` — provider retry and provider observability.
* :mod:`tau2.harness.a3` — reserved for future recovery work.

The legacy ``tau2.harness.<module>`` paths remain compatibility aliases so
existing commands, scripts, and tests do not need an import-path migration.
"""

from tau2.harness.a1.contracts import (
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
from tau2.harness.a1.parser import (
    ParserBatchResult,
    ParserBoundaryError,
    ParserFailure,
    ParserNormalizer,
    RawToolCall,
    SyntheticIdCollisionError,
)
from tau2.harness.a1.registry import (
    RegistryPreflightError,
    RegistrySnapshot,
    RegistryTool,
    RegistryValidationResult,
    RegistryValidator,
    RequestorValidator,
    build_registry_snapshot,
)
from tau2.harness.a1.validation import (
    APPROVED_DOMAIN_CONSTRAINTS_SHA256,
    StrictSchemaDomainValidator,
)
from tau2.harness.a1.dispatcher import (
    A1ValidationExecutor,
    DuplicateExecutionError,
    NativeToolExecutor,
)
from tau2.harness.a1.trace import TraceInvariantError, TraceLogger, TraceLoggerError

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

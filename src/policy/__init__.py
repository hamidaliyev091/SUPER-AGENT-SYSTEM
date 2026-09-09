"""Policy package (Phase 3) - deterministic authorization gate."""
from .canonical import Canonicalizer, match_scope
from .engine import (
    EXECUTION_PERMITTING_STATES,
    PolicyEngine,
    ProtectedPathRegistry,
    RuntimePathMapping,
)
from .registry import (
    COMPATIBLE_VERSIONS,
    MATRIX,
    MODE_INDEX,
    OPERATIONS,
    OperationRule,
    PROTECTED_PATH_CLASSES,
)

__all__ = [
    "Canonicalizer",
    "COMPATIBLE_VERSIONS",
    "EXECUTION_PERMITTING_STATES",
    "MATRIX",
    "MODE_INDEX",
    "OPERATIONS",
    "OperationRule",
    "PolicyEngine",
    "PROTECTED_PATH_CLASSES",
    "ProtectedPathRegistry",
    "RuntimePathMapping",
    "match_scope",
]

"""Verification package (Phase 5) - independent verification of task results."""
from .android_methods import (
    REQUIRED_REQUIREMENTS,
    criterion_for,
    default_methods,
    parse_requirements,
)
from .engine import VerificationEngine, VerificationError, VerificationReport
from .independence import IndependenceResult
from .methods import VerificationMethodSpec, output_contains, output_satisfies
from .result_store import VerificationResultError, VerificationResultStore

__all__ = [
    "IndependenceResult",
    "REQUIRED_REQUIREMENTS",
    "VerificationEngine",
    "VerificationError",
    "VerificationMethodSpec",
    "VerificationReport",
    "VerificationResultError",
    "VerificationResultStore",
    "criterion_for",
    "default_methods",
    "output_contains",
    "output_satisfies",
    "parse_requirements",
]

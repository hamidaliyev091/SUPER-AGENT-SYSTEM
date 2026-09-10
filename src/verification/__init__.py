"""Verification package (Phase 5) - independent verification of task results."""
from .engine import VerificationEngine, VerificationError, VerificationReport
from .independence import IndependenceResult
from .methods import VerificationMethodSpec, output_contains, output_satisfies
from .result_store import VerificationResultError, VerificationResultStore

__all__ = [
    "IndependenceResult",
    "VerificationEngine",
    "VerificationError",
    "VerificationMethodSpec",
    "VerificationReport",
    "VerificationResultError",
    "VerificationResultStore",
    "output_contains",
    "output_satisfies",
]

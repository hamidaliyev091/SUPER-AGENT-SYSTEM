"""Termux platform adapters (Phase 12)."""
from .environment import TermuxEnvironmentAdapter
from .filesystem import TermuxFilesystemAdapter
from .runtime import TermuxRuntimeAdapter

__all__ = [
    "TermuxEnvironmentAdapter",
    "TermuxFilesystemAdapter",
    "TermuxRuntimeAdapter",
]

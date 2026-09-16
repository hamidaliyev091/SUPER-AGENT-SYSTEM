"""Termux platform adapters (Phases 12-14)."""
from .android import TermuxAndroidAdapter
from .environment import TermuxEnvironmentAdapter
from .filesystem import TermuxFilesystemAdapter
from .runtime import TermuxRuntimeAdapter

__all__ = [
    "TermuxAndroidAdapter",
    "TermuxEnvironmentAdapter",
    "TermuxFilesystemAdapter",
    "TermuxRuntimeAdapter",
]

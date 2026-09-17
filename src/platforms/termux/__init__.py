"""Termux platform adapters (Phases 12-14)."""
from .android import TermuxAndroidAdapter
from .android_bridge import AndroidBridgeError, AndroidCapabilityBridge
from .android_capabilities import TermuxAndroidCapabilityAdapter
from .environment import TermuxEnvironmentAdapter
from .filesystem import TermuxFilesystemAdapter
from .runtime import TermuxRuntimeAdapter

__all__ = [
    "AndroidBridgeError",
    "AndroidCapabilityBridge",
    "TermuxAndroidAdapter",
    "TermuxAndroidCapabilityAdapter",
    "TermuxEnvironmentAdapter",
    "TermuxFilesystemAdapter",
    "TermuxRuntimeAdapter",
]

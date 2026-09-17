"""TermuxAndroidCapabilityAdapter: SAS tool surface for the real device.

One Tool per registered capability operation, each backed by exactly one
named operation on the Android bridge app
(docs/implementation/ANDROID_CAPABILITIES.md). The adapter is an executor:
by the time `execute` runs, Policy has already authorized the operation and
the pipeline has already journaled the decision.

Rules this adapter enforces on its own side of the boundary:

- Strict argument validation. Policy validates the registry-declared
  arguments; the adapter additionally validates the arguments its own
  protocol needs, so a malformed call fails here rather than on the device.
- Structured results. Every outcome is a ToolResult with a stable error
  code; a device failure never becomes an exception the loop cannot see.
- Evidence-backed observations. A screenshot is only accepted when the
  bytes match the digest the device declared and are a real PNG. The
  artifact is written content-addressed; the ToolResult carries only its
  descriptor, never the image.
- Conservative side-effect accounting. A failure that might have taken
  effect (timeout, dropped connection) is reported as
  SideEffectState.UNKNOWN, which blocks completion and forces recovery to
  verify before retrying. Only failures the device reported as not-having-
  happened are KNOWN_FAILED.
- No shell. There is no operation that runs a command, and the operation
  name is the only thing that selects behaviour.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from typing import Dict, Optional

from core import Error, Tool, ToolResult, utcnow_iso
from core.enums import Idempotency, Reversibility, RiskLevel, SideEffect, SideEffectState

from .android_bridge import (
    GLOBAL_ACTION,
    LAUNCH_PACKAGE,
    OBSERVE_UI,
    OPEN_URL,
    SCREENSHOT,
    TAP,
    TYPE_TEXT,
    AndroidBridgeError,
    AndroidCapabilityBridge,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_PACKAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._]*")
_GLOBAL_ACTIONS = ("BACK", "HOME")

#: Operation metadata. Mirrors src/policy/registry.py for these operations;
#: the registry remains the authority (Tool metadata is descriptive only,
#: INTERFACES.md s11) and a test asserts the two never drift.
_TOOL_SPECS = {
    SCREENSHOT: (RiskLevel.LOW, SideEffect.READ_ONLY, Reversibility.REVERSIBLE,
                 Idempotency.IDEMPOTENT),
    OBSERVE_UI: (RiskLevel.LOW, SideEffect.READ_ONLY, Reversibility.REVERSIBLE,
                 Idempotency.IDEMPOTENT),
    LAUNCH_PACKAGE: (RiskLevel.MEDIUM, SideEffect.MUTATING, Reversibility.REVERSIBLE,
                     Idempotency.CONDITIONALLY_IDEMPOTENT),
    OPEN_URL: (RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT, Reversibility.UNKNOWN,
               Idempotency.UNKNOWN),
    GLOBAL_ACTION: (RiskLevel.MEDIUM, SideEffect.MUTATING, Reversibility.REVERSIBLE,
                    Idempotency.CONDITIONALLY_IDEMPOTENT),
    TAP: (RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT, Reversibility.UNKNOWN,
          Idempotency.UNKNOWN),
    TYPE_TEXT: (RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT, Reversibility.UNKNOWN,
                Idempotency.UNKNOWN),
}

#: Every capability operation acts on the one foreground device, so they
#: share a single named resource lock (execution/resources.py).
FOREGROUND_RESOURCE = "ANDROID_FOREGROUND"

#: Server codes that mean "the device refused before anything happened".
#: Anything else on an action is treated as a possibly-applied side effect.
_NO_EFFECT_CODES = frozenset({
    "UNAUTHORIZED", "CAPABILITY_DISABLED", "OPERATION_NOT_SUPPORTED",
    "BAD_REQUEST", "INVALID_ARGUMENT", "PACKAGE_NOT_LAUNCHABLE",
    "UI_UNAVAILABLE", "ACTION_FAILED", "STALE_OBSERVATION",
    "CAPABILITY_UNAVAILABLE",
})

#: Read-only failures a bounded retry may re-attempt. Action failures are
#: never listed: an action whose effect is unknown must be verified first.
_RETRYABLE_CODES = frozenset({
    "CAPABILITY_UNAVAILABLE", "STALE_OBSERVATION", "SCREENSHOT_FAILED",
    "CAPABILITY_ERROR", "TIMEOUT", "CONNECTION", "PROTOCOL",
})

_TRANSPORT_TIMEOUT = "TIMEOUT"
_TRANSPORT_UNAVAILABLE = ("CONNECTION", "LOOPBACK", "CONFIG", "PROTOCOL")


def png_dimensions(raw: bytes):
    """(width, height) of a PNG, or (None, None) when it is not one."""
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        return None, None
    width, height = struct.unpack(">II", raw[16:24])
    return int(width), int(height)


class TermuxAndroidCapabilityAdapter:
    """Tool surface over the Android capability bridge."""

    def __init__(self, bridge: AndroidCapabilityBridge, artifacts):
        self.bridge = bridge
        self.artifacts = artifacts

    def tools(self) -> Dict[str, Tool]:
        return {
            SCREENSHOT: self._tool(SCREENSHOT, self._screenshot,
                                   "Capture the device screen"),
            OBSERVE_UI: self._tool(OBSERVE_UI, self._observe_ui,
                                   "Observe the foreground app and its UI tree"),
            LAUNCH_PACKAGE: self._tool(LAUNCH_PACKAGE, self._launch_package,
                                       "Launch an installed app by package id"),
            OPEN_URL: self._tool(OPEN_URL, self._open_url,
                                 "Open an https URL in the device browser"),
            GLOBAL_ACTION: self._tool(GLOBAL_ACTION, self._global_action,
                                      "Perform a global navigation action (BACK, HOME)"),
            TAP: self._tool(TAP, self._tap,
                            "Activate a UI node from a retained observation"),
            TYPE_TEXT: self._tool(TYPE_TEXT, self._type_text,
                                  "Enter text into a UI node from a retained observation"),
        }

    def _tool(self, operation_id, operation, description) -> Tool:
        risk, side_effect, reversibility, idempotency = _TOOL_SPECS[operation_id]
        return Tool(
            id=operation_id, name=operation_id,
            description=f"Android {description} via the capability bridge",
            inputSchema={}, outputSchema={}, riskLevel=risk,
            sideEffect=side_effect, reversibility=reversibility,
            operationId=operation_id, idempotency=idempotency,
            resourceRequirements=[FOREGROUND_RESOURCE], execute=operation)

    # -- observation operations ---------------------------------------------

    def _screenshot(self, args, context):
        task_id = self._task_id(context)
        if task_id is None:
            return self._failure("ANDROID_OPERATION_FAILED",
                                 "screenshot requires a task context")
        try:
            data = self.bridge.observe(SCREENSHOT)
        except AndroidBridgeError as exc:
            return self._bridge_failure(exc, read_only=True)
        raw = self._decode_image(data)
        if raw is None:
            # A transfer that does not match its own digest is not an
            # observation; failing closed is the only safe reading.
            return self._failure(
                "ANDROID_OPERATION_FAILED",
                "screenshot payload failed integrity verification")
        artifact = self.artifacts.write_artifact(task_id, raw, ".png")
        width, height = png_dimensions(raw)
        return ToolResult(
            success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
            timestamp=utcnow_iso(),
            output={"artifact": {"kind": "image", **artifact,
                                 "width": width, "height": height},
                    "capturedAt": data.get("capturedAt")})

    def _observe_ui(self, args, context):
        try:
            data = self.bridge.observe(OBSERVE_UI)
        except AndroidBridgeError as exc:
            return self._bridge_failure(exc, read_only=True)
        return ToolResult(
            success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
            timestamp=utcnow_iso(), output=data)

    # -- action operations ---------------------------------------------------

    def _launch_package(self, args, context):
        package = args.get("package")
        if not isinstance(package, str) or not _PACKAGE_RE.fullmatch(package):
            return self._failure("ANDROID_OPERATION_FAILED",
                                 "launch requires a valid package id")
        return self._act(LAUNCH_PACKAGE, {"package": package})

    def _open_url(self, args, context):
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return self._failure("ANDROID_OPERATION_FAILED", "open_url requires a url")
        return self._act(OPEN_URL, {"url": url})

    def _global_action(self, args, context):
        action = args.get("action")
        if action not in _GLOBAL_ACTIONS:
            return self._failure(
                "ANDROID_OPERATION_FAILED",
                f"global_action must be one of {', '.join(_GLOBAL_ACTIONS)}")
        return self._act(GLOBAL_ACTION, {"action": action})

    def _tap(self, args, context):
        node = self._node_arguments(args)
        if node is None:
            return self._failure(
                "ANDROID_OPERATION_FAILED",
                "tap requires observationId, nodeRef and target")
        return self._act(TAP, node)

    def _type_text(self, args, context):
        node = self._node_arguments(args)
        if node is None:
            return self._failure(
                "ANDROID_OPERATION_FAILED",
                "type_text requires observationId, nodeRef and target")
        text = args.get("text")
        if not isinstance(text, str):
            return self._failure("ANDROID_OPERATION_FAILED", "type_text requires text")
        node["text"] = text
        return self._act(TYPE_TEXT, node)

    # -- shared execution ----------------------------------------------------

    def _act(self, operation: str, arguments: dict) -> ToolResult:
        try:
            data = self.bridge.action(operation, arguments)
        except AndroidBridgeError as exc:
            return self._bridge_failure(exc, read_only=False)
        return ToolResult(success=True,
                          sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso(), output=data)

    @staticmethod
    def _node_arguments(args) -> Optional[dict]:
        """The node reference an accessibility operation acts on. The ref
        is only meaningful inside its observation; the device re-resolves
        it and refuses a stale one."""
        observation_id = args.get("observationId")
        node_ref = args.get("nodeRef")
        target = args.get("target")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return None
        if not isinstance(node_ref, str) or not node_ref.strip():
            return None
        if not isinstance(target, str) or not target.strip():
            return None
        return {"observationId": observation_id, "nodeRef": node_ref,
                "target": target}

    @staticmethod
    def _task_id(context) -> Optional[str]:
        if isinstance(context, dict):
            task_id = context.get("taskId")
            if isinstance(task_id, str) and task_id.strip():
                return task_id
        return None

    @staticmethod
    def _decode_image(data: dict) -> Optional[bytes]:
        """Decode and verify a screenshot payload. Returns None when the
        bytes do not match the declared digest or are not a PNG."""
        encoded = data.get("imageBase64")
        digest = data.get("sha256")
        if not isinstance(encoded, str) or not isinstance(digest, str):
            return None
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not raw.startswith(PNG_MAGIC):
            return None
        if hashlib.sha256(raw).hexdigest() != digest:
            return None
        return raw

    def _bridge_failure(self, exc: AndroidBridgeError, read_only: bool) -> ToolResult:
        """Map a bridge failure onto a structured, conservative ToolResult."""
        code = self._error_code(exc)
        if read_only:
            state = SideEffectState.KNOWN_FAILED
            retryable = exc.code in _RETRYABLE_CODES
        else:
            # An action that timed out or lost its connection may or may not
            # have taken effect; reporting UNKNOWN blocks completion until
            # recovery verifies, and blocks a blind retry.
            state = (SideEffectState.KNOWN_FAILED
                     if exc.code in _NO_EFFECT_CODES else SideEffectState.UNKNOWN)
            retryable = False
        return ToolResult(success=False, sideEffectState=state,
                          timestamp=utcnow_iso(),
                          error=Error(code=code, message=exc.message,
                                      retryable=retryable))

    @staticmethod
    def _error_code(exc: AndroidBridgeError) -> str:
        if exc.code == _TRANSPORT_TIMEOUT:
            return "ANDROID_OPERATION_TIMEOUT"
        if exc.code in ("UNAUTHORIZED", "CAPABILITY_DISABLED"):
            return "ANDROID_UNAUTHORIZED"
        if exc.code in _TRANSPORT_UNAVAILABLE:
            return "ANDROID_CAPABILITY_UNAVAILABLE"
        return "ANDROID_OPERATION_FAILED"

    @staticmethod
    def _failure(code: str, message: str, retryable: bool = False) -> ToolResult:
        return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                          timestamp=utcnow_iso(),
                          error=Error(code=code, message=message, retryable=retryable))

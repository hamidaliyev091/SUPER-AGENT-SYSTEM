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

#: How a node is named by the caller: "<package>#<visible text>". A
#: snapshot id and a node ref are minted by the device and are meaningful
#: only inside the observation that produced them, so a caller cannot know
#: either; it names the node the way a person would, and the ref is
#: resolved here against an observation taken immediately before the act.
NODE_LABEL_SEPARATOR = "#"

#: How many on-screen labels a failed resolution offers back. Bounded: a
#: failure message is a hint, not a node dump.
RESOLUTION_HINT_LIMIT = 8


class NodeResolutionError(Exception):
    """A named UI node could not be resolved to something to act on."""

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


def _bounds(node) -> Optional[list]:
    """A node's [left, top, right, bottom], or None when malformed."""
    bounds = node.get("bounds")
    if (not isinstance(bounds, list) or len(bounds) != 4
            or not all(isinstance(value, int) for value in bounds)):
        return None
    return bounds


def _area(node) -> int:
    bounds = _bounds(node)
    if bounds is None:
        return 0
    left, top, right, bottom = bounds
    return max(0, right - left) * max(0, bottom - top)


def _contains(outer, inner) -> bool:
    """Whether `outer` holds the point a tap on `inner` would land on."""
    box, point = _bounds(outer), _bounds(inner)
    if box is None or point is None:
        return False
    centre_x = (point[0] + point[2]) // 2
    centre_y = (point[1] + point[3]) // 2
    return box[0] <= centre_x <= box[2] and box[1] <= centre_y <= box[3]


def _visible_labels(data) -> str:
    """The labelled nodes of an observation, bounded, for a failure
    message that lets the caller name a node that is actually there."""
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "nothing"
    labels = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("text", "")).strip()
        if text:
            labels.append(repr(text))
        if len(labels) >= RESOLUTION_HINT_LIMIT:
            break
    return ", ".join(labels) if labels else "no labelled nodes"


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
                            "Activate the UI node labelled <package>#<text>"),
            TYPE_TEXT: self._tool(TYPE_TEXT, self._type_text,
                                  "Enter text into the UI node labelled <package>#<text>"),
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
        try:
            node = self._node_arguments(args)
        except NodeResolutionError as exc:
            return self._failure("ANDROID_OPERATION_FAILED", str(exc))
        return self._act(TAP, node)

    def _type_text(self, args, context):
        try:
            node = self._node_arguments(args)
        except NodeResolutionError as exc:
            return self._failure("ANDROID_OPERATION_FAILED", str(exc))
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

    def _node_arguments(self, args) -> dict:
        """The node reference an accessibility operation acts on.

        An explicit (observationId, nodeRef, target) triple is passed
        through as given: the device re-resolves it against its retained
        snapshots and refuses a stale one. Otherwise the node is named as
        "<package>#<visible text>" and resolved here.

        Resolution observes the device first, so the ref it returns was
        minted moments before the act and cannot be stale. It grants
        nothing: the observation is read-only, and Policy has already
        evaluated this operation on the target the caller named.

        Raises NodeResolutionError when the named node is not on screen.
        """
        observation_id = args.get("observationId")
        node_ref = args.get("nodeRef")
        target = args.get("target")
        if all(isinstance(value, str) and value.strip()
               for value in (observation_id, node_ref, target)):
            return {"observationId": observation_id, "nodeRef": node_ref,
                    "target": target}
        if not isinstance(target, str) or not target.strip():
            raise NodeResolutionError(
                "the operation needs a target naming the node, as "
                "<package>#<visible text>")
        return self._resolve_node(target)

    def _resolve_node(self, target: str) -> dict:
        package, separator, label = target.partition(NODE_LABEL_SEPARATOR)
        package, label = package.strip(), label.strip()
        if not separator or not package or not label:
            raise NodeResolutionError(
                f"target {target!r} is not <package>#<visible text>")
        try:
            data = self.bridge.observe(OBSERVE_UI)
        except AndroidBridgeError as exc:
            raise NodeResolutionError(
                f"the device could not be observed to resolve {target!r}: "
                f"{self._error_code(exc)}") from exc
        node_ref = self._match_node(data, package, label)
        if node_ref is None:
            raise NodeResolutionError(
                f"no enabled node matching {label!r} in {package} "
                f"(on screen: {_visible_labels(data)})")
        snapshot_id = data.get("snapshotId")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise NodeResolutionError(
                "the device observation carried no snapshot id")
        return {"observationId": snapshot_id, "nodeRef": node_ref,
                "target": target}

    @staticmethod
    def _match_node(data, package: str, label: str) -> Optional[str]:
        """The ref of the node `label` names, in `package`.

        A row's label usually lives in a child view that is not itself
        clickable, so when no matching node is clickable the smallest
        clickable container holding it is used instead - the thing a
        person tapping the row would actually hit. The package is checked
        as well: a node in another app is never what was named.
        """
        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            return None
        wanted = label.casefold()
        scoped = [node for node in nodes
                  if isinstance(node, dict)
                  and node.get("packageName") == package
                  and node.get("enabled", True)]
        matches = [node for node in scoped
                   if str(node.get("text", "")).strip().casefold() == wanted]
        if not matches:
            matches = [node for node in scoped
                       if wanted in str(node.get("text", "")).casefold()]
        if not matches:
            return None
        for node in matches:
            if node.get("clickable"):
                return node.get("ref")
        best = None
        for candidate in scoped:
            if not candidate.get("clickable"):
                continue
            if not any(_contains(candidate, node) for node in matches):
                continue
            area = _area(candidate)
            if best is None or area < best[0]:
                best = (area, candidate.get("ref"))
        if best is not None:
            return best[1]
        return matches[0].get("ref")

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

"""Reference implementation of the Android capability contract (Phase 14).

TEST USE ONLY - not part of the production path; the Android bridge app
implements the real server (docs/implementation/ANDROID_CAPABILITIES.md).

Serves GET /v1/android/state and POST /v1/android/execute over loopback
with a scripted device, the same bearer-token rule as the app, and
structured errors - so the whole governed path (adapter, policy, pipeline,
verification, completion) can be exercised without a phone attached. The
device here is what a test says it is: it holds a foreground package, a UI
node list, screenshot bytes, and a record of every operation that actually
reached it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

CAPABILITY_VERSION = "1.0"

OBSERVE_UI = "android.observe_ui"
SCREENSHOT = "android.screenshot"
LAUNCH_PACKAGE = "android.launch_package"
OPEN_URL = "android.open_url"
GLOBAL_ACTION = "android.global_action"
TAP = "accessibility.tap"
TYPE_TEXT = "accessibility.type_text"

OPERATIONS = (SCREENSHOT, OBSERVE_UI, LAUNCH_PACKAGE, OPEN_URL, GLOBAL_ACTION,
              TAP, TYPE_TEXT)

BROWSER_PACKAGE = "com.android.browser"


def png_bytes(width: int = 1080, height: int = 2400,
              padding: bytes = b"") -> bytes:
    """A minimal PNG-shaped artifact with real header dimensions. The
    adapter verifies the magic and the digest, and reads the size from the
    IHDR, which is exactly what the device's encoder guarantees."""
    header = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    return b"\x89PNG\r\n\x1a\n" + header + b"\x08\x02\x00\x00\x00" + padding


class CapabilityFailure(Exception):
    """Raise to make the reference server return a structured failure."""

    def __init__(self, code: str, message: str, status: int = 200):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class FakeDevice:
    """A scripted phone behind the capability contract."""

    def __init__(self, token: str = "test-token",
                 foreground: str = "com.termux",
                 launchable: Optional[List[str]] = None):
        self.token = token
        self.foregroundPackage = foreground
        self.previousForegroundPackage: Optional[str] = None
        self.nodes: List[dict] = [
            {"ref": "n0", "text": "Network & internet", "viewId": "android:id/title",
             "className": "android.widget.TextView", "packageName": foreground,
             "clickable": True, "editable": False, "enabled": True,
             "scrollable": False, "bounds": [0, 100, 1080, 200]},
            {"ref": "n1", "text": "Battery", "viewId": "android:id/title",
             "className": "android.widget.TextView", "packageName": foreground,
             "clickable": True, "editable": False, "enabled": True,
             "scrollable": False, "bounds": [0, 200, 1080, 300]},
        ]
        self.snapshotId = "snap-1"
        self.image = png_bytes()
        self.launchable = list(launchable or [])
        self.available = True
        self.accessibilityConnected = True
        self.canTakeScreenshot = True
        self.reason = "ready"
        #: What actually ran on the device, in order.
        self.operations: List[str] = []
        #: What the client asked for, in order - including attempts the
        #: device refused, so a test can tell "was never asked" from
        #: "was asked and refused".
        self.attempts: List[str] = []
        self.arguments: List[Tuple[str, dict]] = []
        self._failures: Dict[str, List[str]] = {}

    # -- scripting -----------------------------------------------------------

    def fail(self, operation: str, code: str = "CAPABILITY_UNAVAILABLE",
             times: int = 1) -> None:
        """Make the next `times` executions of `operation` fail."""
        self._failures.setdefault(operation, []).extend([code] * times)

    def count(self, operation: str) -> int:
        """How many times the operation actually ran."""
        return self.operations.count(operation)

    def asked(self, operation: str) -> int:
        """How many times the client attempted the operation."""
        return self.attempts.count(operation)

    def texts(self) -> List[str]:
        return [str(node.get("text", "")) for node in self.nodes]

    # -- protocol ------------------------------------------------------------

    def state_payload(self) -> dict:
        return {
            "ok": True,
            "data": {
                "available": self.available,
                "accessibilityConnected": self.accessibilityConnected,
                "canTakeScreenshot": self.canTakeScreenshot,
                "capabilityVersion": CAPABILITY_VERSION,
                "operations": list(OPERATIONS) if self.available else [],
                "reason": self.reason,
            },
        }

    def execute(self, operation: str, arguments: dict) -> dict:
        self.attempts.append(operation)
        if operation not in OPERATIONS:
            raise CapabilityFailure(
                "OPERATION_NOT_SUPPORTED",
                f"{operation!r} is not a capability operation")
        pending = self._failures.get(operation) or []
        if pending:
            raise CapabilityFailure(pending.pop(0), f"{operation} is not available")
        if not self.available:
            raise CapabilityFailure("CAPABILITY_DISABLED",
                                    "android control is switched off in the app")
        # Recorded only past every refusal: the log is what actually
        # reached the device.
        self.operations.append(operation)
        self.arguments.append((operation, dict(arguments)))
        handler = getattr(self, "_op_" + operation.replace(".", "_"))
        return handler(arguments)

    # -- operations ----------------------------------------------------------

    def _op_android_observe_ui(self, arguments: dict) -> dict:
        if not self.accessibilityConnected:
            raise CapabilityFailure("UI_UNAVAILABLE",
                                    "the accessibility service is not connected")
        return {
            "snapshotId": self.snapshotId,
            "foregroundPackage": self.foregroundPackage,
            "previousForegroundPackage": self.previousForegroundPackage,
            "display": {"width": 1080, "height": 2400, "densityDpi": 420,
                        "rotation": 0},
            "nodeCount": len(self.nodes),
            "truncated": False,
            "nodes": [dict(node) for node in self.nodes],
        }

    def _op_android_screenshot(self, arguments: dict) -> dict:
        raw = self.image
        digest = hashlib.sha256(raw).hexdigest()
        return {"sha256": digest, "bytes": len(raw),
                "imageBase64": base64.b64encode(raw).decode("ascii"),
                "capturedAt": "2026-09-18T00:00:00.000000Z"}

    def _op_android_launch_package(self, arguments: dict) -> dict:
        package = arguments.get("package")
        if not isinstance(package, str) or not package:
            raise CapabilityFailure("INVALID_ARGUMENT", "package is required")
        if package not in self.launchable and package != self.foregroundPackage:
            raise CapabilityFailure("PACKAGE_NOT_LAUNCHABLE",
                                    f"no launchable activity for {package}")
        self.previousForegroundPackage = self.foregroundPackage
        self.foregroundPackage = package
        return {"performed": True, "target": package}

    def _op_android_open_url(self, arguments: dict) -> dict:
        url = arguments.get("url")
        if not isinstance(url, str) or not url:
            raise CapabilityFailure("INVALID_ARGUMENT", "url is required")
        self.previousForegroundPackage = self.foregroundPackage
        self.foregroundPackage = BROWSER_PACKAGE
        return {"performed": True, "url": url}

    def _op_android_global_action(self, arguments: dict) -> dict:
        action = arguments.get("action")
        if action not in ("BACK", "HOME"):
            raise CapabilityFailure("INVALID_ARGUMENT",
                                    "action must be BACK or HOME")
        if action == "HOME":
            self.previousForegroundPackage = self.foregroundPackage
            self.foregroundPackage = "com.android.launcher"
        return {"performed": True, "action": action}

    def _op_accessibility_tap(self, arguments: dict) -> dict:
        node = self._resolve_node(arguments)
        return {"performed": True, "nodeRef": node["ref"], "text": node.get("text", "")}

    def _op_accessibility_type_text(self, arguments: dict) -> dict:
        node = self._resolve_node(arguments)
        text = arguments.get("text")
        if not isinstance(text, str):
            raise CapabilityFailure("INVALID_ARGUMENT", "text is required")
        node["text"] = text
        return {"performed": True, "nodeRef": node["ref"], "text": text}

    def _resolve_node(self, arguments: dict) -> dict:
        if arguments.get("observationId") != self.snapshotId:
            raise CapabilityFailure("STALE_OBSERVATION",
                                    "the observation is no longer current")
        ref = arguments.get("nodeRef")
        for node in self.nodes:
            if node["ref"] == ref:
                return node
        raise CapabilityFailure("INVALID_ARGUMENT", f"unknown node {ref!r}")


class _Handler(BaseHTTPRequestHandler):

    server_version = "CapabilityReference/1.0"

    def log_message(self, *args):
        pass  # keep test output quiet

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: str, message: str, status: int = 200) -> None:
        self._send_json(status, {"ok": False,
                                 "error": {"code": code, "message": message}})

    def _authorized(self) -> bool:
        expected = "Bearer " + self.server.device.token
        return self.headers.get("Authorization", "") == expected

    def do_GET(self):
        if self.path == "/v1/health":
            self._send_json(200, {"status": "ok", "models": [], "loaded": []})
            return
        if self.path != "/v1/android/state":
            self._error("NOT_FOUND", self.path, status=404)
            return
        if not self._authorized():
            self._error("UNAUTHORIZED", "capability token missing or wrong",
                        status=401)
            return
        self._send_json(200, self.server.device.state_payload())

    def do_POST(self):
        if self.path != "/v1/android/execute":
            self._error("NOT_FOUND", self.path, status=404)
            return
        if not self._authorized():
            self._error("UNAUTHORIZED", "capability token missing or wrong",
                        status=401)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error("BAD_REQUEST", "malformed JSON body", status=400)
            return
        operation = body.get("operation")
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            self._error("BAD_REQUEST", "arguments must be an object")
            return
        try:
            data = self.server.device.execute(str(operation), arguments)
        except CapabilityFailure as failure:
            self._error(failure.code, failure.message, status=failure.status)
            return
        self._send_json(200, {"ok": True, "data": data})


def serve(device: FakeDevice, port: int = 0):
    """Start the reference capability server on a loopback port and return
    (server, thread, base_url). port=0 picks a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.device = device
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"

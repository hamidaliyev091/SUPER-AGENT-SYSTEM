"""AndroidCapabilityBridge (SAS side of the Android capability contract).

A stdlib-only HTTP client for the governed capability endpoints of the
Android bridge app (docs/implementation/ANDROID_CAPABILITIES.md). The
endpoints expose a CLOSED set of named device operations - screenshot,
UI observation, package launch, URL open, global navigation, and
accessibility tap/type on a node from a retained observation. There is no
shell operation and no way to name one: the operation name is the only
thing that selects behaviour, and the app rejects anything else.

Boundaries:

- This client is a transport. It holds no authority: every operation it
  carries has already been authorized by SAS Policy, and the app executes
  it as a dumb executor.
- Loopback only, and the capability endpoints require a bearer token, so
  a hostile app on the phone cannot drive the device through them.
  The token is never logged, never journaled, and never placed in a
  ToolResult.
- Failures raise AndroidBridgeError with a stable code so the adapter can
  map them onto structured ToolResults without crashing the loop.
"""
from __future__ import annotations

import os
from typing import Optional

from .bridge_http import (
    DEFAULT_BRIDGE_URL,
    BridgeError,
    loopback_host,
    request_json,
)

#: The closed operation set. Observation operations are read-only; action
#: operations change device state. Both are named, never composed.
SCREENSHOT = "android.screenshot"
OBSERVE_UI = "android.observe_ui"
LAUNCH_PACKAGE = "android.launch_package"
OPEN_URL = "android.open_url"
GLOBAL_ACTION = "android.global_action"
TAP = "accessibility.tap"
TYPE_TEXT = "accessibility.type_text"

OBSERVE_OPERATIONS = (SCREENSHOT, OBSERVE_UI)
ACTION_OPERATIONS = (LAUNCH_PACKAGE, OPEN_URL, GLOBAL_ACTION, TAP, TYPE_TEXT)
CAPABILITY_OPERATIONS = OBSERVE_OPERATIONS + ACTION_OPERATIONS


class AndroidBridgeError(BridgeError):
    """Capability channel failure with a stable, machine-readable code.

    Beyond the transport codes (LOOPBACK/CONFIG/CONNECTION/TIMEOUT/HTTP/
    PROTOCOL), the server's own structured code is surfaced verbatim
    (UNAUTHORIZED, CAPABILITY_DISABLED, OPERATION_NOT_SUPPORTED,
    BAD_REQUEST, INVALID_ARGUMENT, CAPABILITY_UNAVAILABLE,
    STALE_OBSERVATION, SCREENSHOT_FAILED, ACTION_FAILED, ...), so callers
    can distinguish "denied" from "device refused" from "try again".
    """


#: Server error codes that a bounded retry may re-attempt.
RETRYABLE_SERVER_CODES = frozenset({
    "CAPABILITY_UNAVAILABLE", "STALE_OBSERVATION", "SCREENSHOT_FAILED",
    "CAPABILITY_ERROR",
})


class AndroidCapabilityBridge:
    """Client for the Android capability endpoints."""

    def __init__(self, base_url: Optional[str] = None,
                 token: Optional[str] = None,
                 timeout: Optional[float] = None):
        self.base_url = (base_url or os.environ.get("ANDROID_BRIDGE_URL")
                         or DEFAULT_BRIDGE_URL).rstrip("/")
        loopback_host(self.base_url)  # fail closed on configuration errors
        self.token = token if token is not None else os.environ.get(
            "ANDROID_BRIDGE_TOKEN", "")
        try:
            self.timeout = float(timeout if timeout is not None
                                 else os.environ.get("ANDROID_BRIDGE_TIMEOUT",
                                                     "60"))
        except ValueError:
            raise AndroidBridgeError("CONFIG", "ANDROID_BRIDGE_TIMEOUT is not a number")

    # -- public API ----------------------------------------------------------

    def state(self) -> dict:
        """Capability availability as the device reports it. Requires the
        bearer token: availability is not public information."""
        return self._request("GET", "/v1/android/state")

    def execute(self, operation: str, arguments: Optional[dict] = None) -> dict:
        """Run one named operation; returns the operation's data payload.

        Any failure - transport, authorization, or the device refusing the
        operation - raises AndroidBridgeError. A missing `ok` field is
        treated as failure: the client never assumes success.
        """
        if operation not in CAPABILITY_OPERATIONS:
            raise AndroidBridgeError(
                "OPERATION_NOT_SUPPORTED",
                f"{operation!r} is not one of the closed capability operations")
        body = {"operation": operation, "arguments": dict(arguments or {})}
        result = self._request("POST", "/v1/android/execute", body=body)
        if result.get("ok") is not True:
            error = result.get("error") or {}
            code = str(error.get("code") or "CAPABILITY_ERROR")
            message = str(error.get("message") or "capability execution failed")
            raise AndroidBridgeError(code, message)
        data = result.get("data")
        if not isinstance(data, dict):
            raise AndroidBridgeError("PROTOCOL", "capability response has no data object")
        return data

    def observe(self, operation: str, arguments: Optional[dict] = None) -> dict:
        """Run one observation operation (screenshot / UI observation)."""
        if operation not in OBSERVE_OPERATIONS:
            raise AndroidBridgeError(
                "OPERATION_NOT_SUPPORTED", f"{operation!r} is not an observation operation")
        return self.execute(operation, arguments)

    def action(self, operation: str, arguments: Optional[dict] = None) -> dict:
        """Run one action operation."""
        if operation not in ACTION_OPERATIONS:
            raise AndroidBridgeError(
                "OPERATION_NOT_SUPPORTED", f"{operation!r} is not an action operation")
        return self.execute(operation, arguments)

    # -- transport -----------------------------------------------------------

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        headers = {}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            return request_json(self.base_url, path, method=method, body=body,
                                headers=headers, timeout=self.timeout,
                                error=AndroidBridgeError)
        except AndroidBridgeError as exc:
            # Surface the server's own structured code when it sent one,
            # so "denied" stays distinguishable from "device failed".
            payload = exc.payload or {}
            error = payload.get("error")
            if isinstance(error, dict) and error.get("code"):
                raise AndroidBridgeError(
                    str(error["code"]),
                    str(error.get("message") or exc.message),
                    status=exc.status, payload=payload)
            raise

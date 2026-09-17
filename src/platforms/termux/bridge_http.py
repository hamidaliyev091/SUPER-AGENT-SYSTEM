"""Shared loopback HTTP transport for the Android bridge clients.

Two clients speak to the same Android loopback server (127.0.0.1:8765):
GenieXBridge (inference, docs/implementation/GENIEX_BRIDGE.md) and
AndroidCapabilityBridge (governed device capabilities,
docs/implementation/ANDROID_CAPABILITIES.md). Only the transport is shared;
each client keeps its own contract and its own error type.

Security rules enforced here, once, for both:

- loopback only: a non-loopback bridge URL is refused (the bridge is a
  local IPC channel, never a remote host);
- failures raise BridgeError with a stable code (LOOPBACK, CONFIG,
  CONNECTION, TIMEOUT, HTTP, PROTOCOL) so callers fail safely without
  crashing the governed loop. The structured error body, when the server
  sent one, is attached as `payload` so a client can re-raise the
  server's own code.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class BridgeError(Exception):
    """Bridge failure with a stable, machine-readable code."""

    def __init__(self, code: str, message: str, status: Optional[int] = None,
                 payload: Optional[dict] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.payload = payload


def loopback_host(url: str) -> str:
    """Returns the host of a loopback URL; raises LOOPBACK otherwise."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if host not in _LOOPBACK_HOSTS:
        raise BridgeError(
            "LOOPBACK", f"bridge URL must be loopback-only, got {host!r}")
    return host


def _error_payload(exc: urllib.error.HTTPError) -> Optional[dict]:
    # An HTTPError is a file-like response: the body is read and the
    # connection closed, so a refused request does not leak a socket until
    # the garbage collector happens to run.
    try:
        with exc:
            decoded = json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def request_json(base_url: str, path: str, method: str = "GET",
                 body: Optional[dict] = None, headers: Optional[dict] = None,
                 timeout: float = 30.0, error=BridgeError) -> dict:
    """One JSON request/response over the loopback bridge.

    `error` is the exception class to raise, so each client surfaces its
    own error type while sharing this transport.
    """
    request_headers = {"Accept": "application/json"}
    request_headers.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=data,
                                     headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except socket.timeout:
        raise error("TIMEOUT", f"bridge did not respond within {timeout}s")
    except urllib.error.HTTPError as exc:
        decoded = _error_payload(exc)
        detail = ""
        if decoded:
            detail = str((decoded.get("error") or {}).get("message", ""))
        raise error(
            "HTTP", f"{path} returned {exc.code}" + (f": {detail}" if detail else ""),
            status=exc.code, payload=decoded)
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        raise error("CONNECTION", f"bridge unreachable at {base_url}: {exc}")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        raise error("PROTOCOL", f"bridge returned non-JSON: {payload[:120]}")
    if not isinstance(decoded, dict):
        raise error("PROTOCOL", "bridge response is not an object")
    return decoded

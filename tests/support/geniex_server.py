"""Reference implementation of the GenieX bridge contract (v1).

Serves the two endpoints (chat completions + vision) over loopback with a
scripted handler. Doubles as:

- the test double for the SAS-side client and adapters;
- the reference the Android GenieX app endpoint can implement against
  (see docs/implementation/GENIEX_BRIDGE.md).

Stdlib only (http.server); run in a thread within tests.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


class _Handler(BaseHTTPRequestHandler):

    server_version = "GenieXReference/1.0"

    def log_message(self, *args):
        pass  # keep test output quiet

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/health":
            self._send_json(200, {"status": "ok",
                                  "models": self.server.bridge.health_models()})
        else:
            self._send_json(404, {"error": {"code": "NOT_FOUND",
                                            "message": self.path}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"code": "BAD_REQUEST",
                                            "message": "invalid JSON"}})
            return
        if self.path == "/v1/chat/completions":
            self.server.bridge.handle_chat(self, body)
        elif self.path == "/v1/vision":
            self.server.bridge.handle_vision(self, body)
        else:
            self._send_json(404, {"error": {"code": "NOT_FOUND",
                                            "message": self.path}})


class GenieXReferenceBridge:
    """Scripted GenieX behavior: chat/vision handlers with optional
    latency and failure injection for failure/recovery/timeout tests."""

    def __init__(self, chat_handler: Optional[Callable] = None,
                 vision_handler: Optional[Callable] = None,
                 latency: float = 0.0,
                 models: Optional[list] = None):
        self.chat_handler = chat_handler or (lambda request: {
            "content": "", "toolCalls": [],
            "usage": {"inputTokens": 1, "outputTokens": 1},
            "finishReason": "stop"})
        self.vision_handler = vision_handler or (lambda request: {
            "content": "default vision description",
            "usage": {"inputTokens": 1, "outputTokens": 1}})
        self.latency = latency
        self._models = models or ["qwen3-4b-instruct-2507", "qwen2.5-vl-7b-instruct"]
        self.chat_requests = []
        self.vision_requests = []

    def health_models(self):
        return list(self._models)

    def handle_chat(self, handler, request):
        self.chat_requests.append(request)
        if self.latency:
            import time
            time.sleep(self.latency)
        try:
            response = self.chat_handler(request)
            handler._send_json(200, response)
        except GenieXScriptedFailure as failure:
            handler._send_json(failure.status,
                               {"error": {"code": failure.code,
                                          "message": failure.message}})

    def handle_vision(self, handler, request):
        self.vision_requests.append(request)
        if self.latency:
            import time
            time.sleep(self.latency)
        try:
            response = self.vision_handler(request)
            handler._send_json(200, response)
        except GenieXScriptedFailure as failure:
            handler._send_json(failure.status,
                               {"error": {"code": failure.code,
                                          "message": failure.message}})


class GenieXScriptedFailure(Exception):
    """Raise from a handler to make the reference server return an error."""

    def __init__(self, status: int = 500, code: str = "INFERENCE_FAILED",
                 message: str = "inference failed"):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def serve(bridge: GenieXReferenceBridge, port: int = 0):
    """Start the reference server on a loopback port and return
    (server, thread, base_url). port=0 picks a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.bridge = bridge
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"

"""Reference implementation of the GenieX bridge contract (v2,
OpenAI-compatible chat completions). TEST USE ONLY - not part of the
production path; the Android GenieX app implements the real server.

Serves GET /v1/health and POST /v1/chat/completions (text and multimodal
image content) over loopback with scripted handlers, optional latency and
failure injection, and structured errors for malformed requests.
Stdlib only (http.server); run in a thread within tests.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

DEFAULT_MODELS = ["qwen3-4b-instruct-2507", "qwen2.5-vl-7b-instruct"]


class _Handler(BaseHTTPRequestHandler):

    server_version = "GenieXReference/2.0"

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
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"code": "NOT_FOUND",
                                            "message": self.path}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": {"code": "BAD_REQUEST",
                                            "type": "invalid_request_error",
                                            "message": "malformed JSON body"}})
            return
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            self._send_json(400, {"error": {"code": "BAD_REQUEST",
                                            "type": "invalid_request_error",
                                            "message": "messages must be a list"}})
            return
        self.server.bridge.handle_chat(self, body)


def openai_response(model: str, content: str = "",
                    tool_calls: Optional[list] = None,
                    finish_reason: str = "stop",
                    usage: Optional[dict] = None) -> dict:
    """Build an OpenAI-compatible chat completion response."""
    tool_calls = tool_calls or []
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1758000000,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            },
            "finish_reason": finish_reason,
        }],
        "usage": usage or {"prompt_tokens": 1, "completion_tokens": 1,
                           "total_tokens": 2},
    }


class GenieXReferenceBridge:
    """Scripted GenieX behavior: chat handler with optional latency and
    failure injection for failure/recovery/timeout tests."""

    def __init__(self, chat_handler: Optional[Callable] = None,
                 latency: float = 0.0,
                 models: Optional[list] = None):
        self.chat_handler = chat_handler or (lambda request: openai_response(
            request.get("model", "qwen3-4b-instruct-2507")))
        self.latency = latency
        self._models = models or list(DEFAULT_MODELS)
        self.chat_requests = []

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
